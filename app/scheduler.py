"""
APScheduler-based news automation scheduler.

Flow per cycle:
  :00  - Fetch news, score, generate 3 posts (status=draft)
  +1hr - Check: if any post was manually approved -> post that one
         If none approved -> post the highest-scored one automatically

Runs 3x daily at Dubai times (9am, 12pm, 7pm = UTC 5, 8, 15).
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")
_started = False


async def scheduled_pipeline_cycle():
    """Step 1: Fetch + score + generate 3 posts as drafts."""
    from app.services.newsgen.pipeline import run_scheduled_cycle

    cycle_time = datetime.now(timezone.utc).strftime("%H:%M UTC")
    logger.info("=" * 60)
    logger.info("CYCLE STARTED at %s", cycle_time)
    logger.info("=" * 60)

    try:
        result = await run_scheduled_cycle()
        logger.info("CYCLE COMPLETE: %s", result)

        # Schedule the delayed auto-post 1 hour from now
        post_time = datetime.now(timezone.utc) + timedelta(hours=1)
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

        scheduler.add_job(
            delayed_auto_post,
            DateTrigger(run_date=post_time),
            id=f"delayed_post_{cycle_id}",
            name=f"Delayed Auto-Post ({cycle_id})",
            replace_existing=True,
            kwargs={"cycle_id": cycle_id},
        )
        logger.info("Delayed auto-post scheduled for %s", post_time.strftime("%H:%M UTC"))

    except Exception as e:
        logger.error("CYCLE FAILED: %s", e, exc_info=True)


async def delayed_auto_post(cycle_id=None):
    """
    Step 2 (1 hour later): Pick the best post and publish it.

    Finds the pending_review cycle, gets its post_ids, then:
    1. If any post was manually approved -> post that one
    2. Otherwise -> post the highest-scored draft
    """
    from app.db.session import get_database
    from app.services.social_publisher import SocialPublisher
    from app.core.config import settings
    from bson import ObjectId

    logger.info("=" * 60)
    logger.info("DELAYED AUTO-POST CHECK (cycle: %s)", cycle_id)
    logger.info("=" * 60)

    try:
        db = await get_database()
        now = datetime.utcnow()

        # Find the pending cycle
        cycle = await db.pipeline_cycles.find_one(
            {"status": "pending_review"},
            sort=[("started_at", -1)],
        )

        if not cycle:
            logger.info("No pending_review cycle found, nothing to auto-post")
            return {"posted": False, "reason": "no_pending_cycle"}

        cycle_post_ids = cycle.get("post_ids", [])
        if not cycle_post_ids:
            logger.info("Cycle has no post_ids, nothing to auto-post")
            return {"posted": False, "reason": "no_posts_in_cycle"}

        # Load all posts from this cycle
        post_oids = []
        for pid in cycle_post_ids:
            try:
                post_oids.append(ObjectId(pid) if isinstance(pid, str) else pid)
            except Exception:
                pass

        cycle_posts = []
        async for post in db.posts.find({"_id": {"$in": post_oids}, "published_at": {"$exists": False}}):
            cycle_posts.append(post)

        if not cycle_posts:
            logger.info("All posts in cycle already published or deleted")
            await db.pipeline_cycles.update_one(
                {"_id": cycle["_id"]},
                {"$set": {"status": "completed", "completed_at": now}},
            )
            return {"posted": False, "reason": "no_unpublished_posts"}

        # Priority 1: Manually approved post
        approved_posts = [p for p in cycle_posts if p.get("status") == "approved"]
        # Priority 2: Highest-scored draft
        draft_posts = sorted(
            [p for p in cycle_posts if p.get("status") == "draft"],
            key=lambda p: p.get("relevance_score") or p.get("ai_score") or 0,
            reverse=True,
        )

        if approved_posts:
            chosen = approved_posts[0]
            logger.info("Found manually approved post: %s", chosen.get("headline", "N/A"))
        elif draft_posts:
            chosen = draft_posts[0]
            logger.info("No approved posts, using highest-scored draft: %s (score=%s)",
                        chosen.get("headline", "N/A"), chosen.get("relevance_score"))
        else:
            logger.info("No draft or approved posts in cycle")
            return {"posted": False, "reason": "no_eligible_posts"}

        image_url = chosen.get("image_url") or chosen.get("final_image_path")
        caption = chosen.get("caption", "")
        hashtags = chosen.get("hashtags", [])
        if hashtags:
            caption += "\n\n" + " ".join(f"#{h}" for h in hashtags)

        if not image_url:
            logger.warning("Chosen post has no image, skipping")
            return {"posted": False, "reason": "no_image"}

        if settings.AUTO_POST_MODE == "manual":
            logger.info("Auto-post skipped (mode=manual)")
            return {"posted": False, "reason": "manual_mode"}

        publisher = SocialPublisher()
        platforms = ["instagram"]
        post_results = {}

        for platform in platforms:
            try:
                result = await publisher.publish_single(
                    platform=platform, caption=caption, image_url=image_url,
                )
                post_results[platform] = result
                if result.get("success"):
                    logger.info("Auto-posted to %s: %s", platform, chosen.get("headline", "")[:60])
                else:
                    logger.warning("Auto-post to %s failed: %s", platform, result.get("error"))
            except Exception as e:
                post_results[platform] = {"success": False, "error": str(e)}
                logger.error("Auto-post exception: %s", e)

        any_success = any(r.get("success") for r in post_results.values())
        update = {"auto_post_results": post_results, "auto_posted_at": now, "updated_at": now}
        if any_success:
            update["status"] = "posted"
            update["published_at"] = now

        await db.posts.update_one({"_id": chosen["_id"]}, {"$set": update})

        # Mark cycle as completed
        await db.pipeline_cycles.update_one(
            {"_id": cycle["_id"]},
            {"$set": {"status": "posted" if any_success else "failed", "completed_at": now}},
        )

        logger.info("Auto-post result: success=%s", any_success)
        return {"posted": any_success, "post_id": str(chosen["_id"]), "results": post_results}

    except Exception as e:
        logger.error("DELAYED AUTO-POST FAILED: %s", e, exc_info=True)
        return {"posted": False, "error": str(e)}


async def check_overdue_autoposts():
    """
    Periodic safety net: check every 5 min for overdue auto-posts.
    Catches cases where the one-shot delayed_auto_post job was lost
    due to worker restart.
    """
    try:
        from app.db.session import get_database
        db = await get_database()
        now = datetime.utcnow()

        overdue = await db.pipeline_cycles.find_one({
            "status": "pending_review",
            "auto_post_at": {"$lte": now},
        })
        if overdue:
            logger.info("OVERDUE CHECK: Found missed auto-post for cycle %s, posting now", overdue.get("cycle_id"))
            await delayed_auto_post(cycle_id=overdue.get("cycle_id", "overdue_recovery"))
    except Exception as e:
        logger.warning("Overdue auto-post check failed: %s", e)


def start_scheduler():
    global _started
    if _started:
        return
    from app.core.config import settings
    hours_str = settings.SCHEDULE_HOURS_UTC

    scheduler.add_job(
        scheduled_pipeline_cycle,
        CronTrigger(hour=hours_str, minute="0"),
        id="news_pipeline_cycle",
        name="News Pipeline Cycle",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Check every 5 min for overdue auto-posts (safety net for lost jobs)
    scheduler.add_job(
        check_overdue_autoposts,
        CronTrigger(minute="*/5"),
        id="overdue_autopost_check",
        name="Overdue Auto-Post Check",
        replace_existing=True,
        misfire_grace_time=600,
    )

    scheduler.start()
    _started = True

    # Recovery: check if any pending cycle missed its auto-post (e.g. after restart)
    import asyncio
    async def _check_missed_autopost():
        try:
            from app.db.session import get_database
            db = await get_database()
            now = datetime.utcnow()
            missed = await db.pipeline_cycles.find_one({
                "status": "pending_review",
                "auto_post_at": {"$lte": now},
            })
            if missed:
                logger.info("RECOVERY: Found missed auto-post from cycle %s, running now", missed.get("cycle_id"))
                await delayed_auto_post(cycle_id=missed.get("cycle_id", "recovery"))
        except Exception as e:
            logger.warning("Recovery check failed (non-critical): %s", e)

    # Schedule recovery check 10 seconds after startup
    scheduler.add_job(
        _check_missed_autopost,
        DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=10)),
        id="recovery_check",
        name="Missed Auto-Post Recovery",
        replace_existing=True,
    )

    hours = [int(h.strip()) for h in hours_str.split(",")]
    dubai_gen = [f"{(h+4)%24}:00" for h in hours]
    dubai_post = [f"{(h+4+1)%24}:00" for h in hours]
    logger.info("Scheduler started:")
    logger.info("  Generate: %s Dubai", ", ".join(dubai_gen))
    logger.info("  Auto-post: %s Dubai (+1hr)", ", ".join(dubai_post))
    logger.info("  Mode: %s | Gen: %s | Post: %s", settings.AUTO_POST_MODE, settings.POSTS_TO_GENERATE, settings.POSTS_TO_AUTOPOST)


def get_scheduler_status():
    from app.core.config import settings
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    hours = [int(h.strip()) for h in settings.SCHEDULE_HOURS_UTC.split(",")]
    dubai_gen = [f"{(h+4)%24}:00" for h in hours]
    dubai_post = [f"{(h+4+1)%24}:00" for h in hours]
    return {
        "running": _started and scheduler.running,
        "mode": settings.AUTO_POST_MODE,
        "posts_to_generate": settings.POSTS_TO_GENERATE,
        "posts_to_autopost": settings.POSTS_TO_AUTOPOST,
        "schedule_utc": settings.SCHEDULE_HOURS_UTC,
        "schedule_dubai_generate": ", ".join(dubai_gen),
        "schedule_dubai_post": ", ".join(dubai_post),
        "jobs": jobs,
    }
