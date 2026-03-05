import asyncio
from app.workers.celery_app import app
from app.services.newsgen.pipeline import run_pipeline
from app.services.newsgen.dedupe import cleanup_old_hashes
from app.db.session import get_database
from app.db.models import PostStatus
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@app.task(name='app.workers.tasks.fetch_and_generate_posts')
def fetch_and_generate_posts():
    logger.info("Starting scheduled news fetch task")
    result = asyncio.run(run_pipeline())
    logger.info(f"News fetch task complete: {result}")
    return result


@app.task(name='app.workers.tasks.auto_publish_top_posts')
def auto_publish_top_posts():
    logger.info("Auto-publishing top posts")

    async def _publish():
        db = await get_database()
        top_posts = await db.posts.find({
            "status": PostStatus.APPROVED,
            "published_at": {"$exists": False},
            "ai_score": {"$gte": 8}
        }).sort("ai_score", -1).limit(2).to_list(2)

        published_count = 0
        for post in top_posts:
            await db.posts.update_one(
                {"_id": post["_id"]},
                {"$set": {
                    "status": PostStatus.PUBLISHED,
                    "published_at": datetime.utcnow()
                }}
            )
            published_count += 1
            logger.info(f"Published post: {post.get('headline', 'N/A')}")
        return {"published": published_count}

    result = asyncio.run(_publish())
    logger.info(f"Auto-publish complete: {result}")
    return result


@app.task(name='app.workers.tasks.cleanup_old_posts')
def cleanup_old_posts():
    logger.info("Cleaning up old posts")
    deleted = asyncio.run(cleanup_old_hashes(days=30))
    logger.info(f"Cleanup complete: {deleted} posts deleted")
    return {"deleted": deleted}


@app.task(name='app.workers.tasks.publish_scheduled_posts')
def publish_scheduled_posts():
    """
    Runs every 2 minutes via Celery Beat.

    Handles TWO scheduling models:
    1. Bulk schedule — post has status="scheduled", scheduled_at <= now, scheduled_platforms list.
       Published all platforms at once.
    2. Per-item schedule — post has scheduled_items[] entries each with their own scheduled_at,
       platform, content_type, and status="scheduled".
       Each item is published independently, allowing Instagram Feed/Story/Carousel and different
       platforms to fire at different times.
    """
    logger.info("Checking for scheduled posts due for publishing...")

    async def _process():
        from app.services.social_publisher import SocialPublisher
        from app.utils.media import resolve_media_url

        db = await get_database()
        now = datetime.utcnow()
        publisher = SocialPublisher()
        published_count = 0
        failed_count = 0

        # ── Helper: resolve image URL ────────────────────────────────────────
        def _resolve(url):
            if not url:
                return None
            if url.startswith("http"):
                return url
            return resolve_media_url(url, expires_in=86400)

        # ── Helper: build caption with hashtags ──────────────────────────────
        def _caption(post, platform):
            pd = post.get("platforms", {}).get(platform, {})
            cap = pd.get("caption") or post.get("caption", "")
            ht  = pd.get("hashtags") or post.get("hashtags", [])
            if ht and platform in ("instagram", "facebook", "threads", "linkedin"):
                cap = f"{cap}\n\n{' '.join('#' + t for t in ht)}"
            return cap

        # ════════════════════════════════════════════════════════════════════
        # MODEL 1 — Bulk: status="scheduled" + scheduled_at <= now
        # ════════════════════════════════════════════════════════════════════
        bulk_due = await db.posts.find({
            "status": "scheduled",
            "scheduled_at": {"$lte": now},
            "scheduled_platforms": {"$exists": True, "$ne": []},
        }).to_list(length=50)

        for post in bulk_due:
            post_id = str(post["_id"])
            platforms = post.get("scheduled_platforms", [])
            if not platforms:
                continue

            try:
                image_url = _resolve(post.get("image_url") or post.get("final_image_path"))
                platform_captions = {p: _caption(post, p) for p in platforms}

                carousel_urls = None
                if post.get("carousel_slides"):
                    carousel_urls = [
                        _resolve(s.get("final_image_url"))
                        for s in post["carousel_slides"]
                        if s.get("final_image_url")
                    ]

                results = await publisher.publish_to_platforms(
                    platforms=platforms,
                    caption=post.get("caption", ""),
                    image_url=image_url,
                    hashtags=post.get("hashtags", []),
                    carousel_image_urls=carousel_urls if carousel_urls and len(carousel_urls) >= 2 else None,
                    platform_captions=platform_captions,
                )

                # WhatsApp override recipients
                if post.get("whatsapp_recipients") and "whatsapp" in platforms:
                    results["whatsapp"] = await publisher.publish_whatsapp(
                        caption=platform_captions.get("whatsapp", post.get("caption", "")),
                        image_url=image_url,
                        recipients=post["whatsapp_recipients"],
                    )

                any_success = any(r.get("success") for r in results.values())
                update_doc = {"updated_at": now, "publish_results": results}

                if any_success:
                    update_doc["status"] = "posted"
                    update_doc["published_at"] = now
                    for p, r in results.items():
                        if r.get("success"):
                            update_doc[f"platforms.{p}.status"] = "published"
                            update_doc[f"platforms.{p}.published_at"] = now
                            if r.get("platform_post_id"):
                                update_doc[f"platforms.{p}.platform_post_id"] = r["platform_post_id"]
                    published_count += 1
                    logger.info(f"Bulk scheduled post {post_id} published → {platforms}")
                else:
                    update_doc["status"] = "approved"
                    update_doc["error_message"] = f"Bulk scheduled publish failed: {results}"
                    failed_count += 1
                    logger.error(f"Bulk scheduled post {post_id} failed: {results}")

                await db.posts.update_one({"_id": post["_id"]}, {"$set": update_doc})

            except Exception as e:
                failed_count += 1
                logger.exception(f"Error publishing bulk-scheduled post {post_id}: {e}")
                await db.posts.update_one(
                    {"_id": post["_id"]},
                    {"$set": {"status": "approved", "error_message": str(e), "updated_at": now}},
                )

        # ════════════════════════════════════════════════════════════════════
        # MODEL 2 — Per-item: scheduled_items[] entries due now
        # ════════════════════════════════════════════════════════════════════
        # Find any post that has at least one pending scheduled item whose time has arrived
        items_due = await db.posts.find({
            "scheduled_items": {
                "$elemMatch": {
                    "status": "scheduled",
                    "scheduled_at": {"$lte": now},
                }
            }
        }).to_list(length=100)

        for post in items_due:
            post_id = str(post["_id"])
            items = post.get("scheduled_items", [])
            image_url = _resolve(post.get("image_url") or post.get("final_image_path"))

            # Story/carousel image URLs
            story_url = _resolve(post.get("story_image_url")) or image_url
            carousel_urls = None
            if post.get("carousel_slides"):
                carousel_urls = [
                    _resolve(s.get("final_image_url"))
                    for s in post["carousel_slides"]
                    if s.get("final_image_url")
                ]

            updated_items = list(items)  # copy to mutate status
            any_item_published = False

            for idx, item in enumerate(items):
                if item.get("status") != "scheduled":
                    continue

                item_scheduled_at = item.get("scheduled_at")
                if isinstance(item_scheduled_at, str):
                    try:
                        item_scheduled_at = datetime.fromisoformat(
                            item_scheduled_at.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except Exception:
                        continue
                elif item_scheduled_at is None:
                    continue

                if item_scheduled_at > now:
                    continue  # not due yet

                platform     = item.get("platform", "")
                content_type = item.get("content_type", "feed")
                caption      = _caption(post, platform)

                try:
                    result = await publisher.publish_single(
                        platform=platform,
                        content_type=content_type,
                        caption=caption,
                        image_url=image_url,
                        story_image_url=story_url if content_type == "story" else None,
                        carousel_image_urls=carousel_urls if content_type == "carousel" and carousel_urls and len(carousel_urls) >= 2 else None,
                    )

                    result_key = f"{platform}_{content_type}" if content_type != "feed" else platform

                    if result.get("success"):
                        updated_items[idx] = {**item, "status": "published", "published_at": now.isoformat()}
                        any_item_published = True
                        published_count += 1
                        logger.info(f"Per-item scheduled {post_id} → {platform}/{content_type} published")

                        # Update platform status in DB
                        await db.posts.update_one(
                            {"_id": post["_id"]},
                            {"$set": {
                                f"platforms.{platform}.status": "published",
                                f"platforms.{platform}.published_at": now,
                                f"publish_results.{result_key}": result,
                                "updated_at": now,
                            }},
                        )
                    else:
                        updated_items[idx] = {**item, "status": "failed", "error": result.get("error", "Unknown")}
                        failed_count += 1
                        logger.error(f"Per-item scheduled {post_id} → {platform}/{content_type} failed: {result.get('error')}")

                except Exception as e:
                    updated_items[idx] = {**item, "status": "failed", "error": str(e)}
                    failed_count += 1
                    logger.exception(f"Per-item scheduled {post_id} → {platform}/{content_type} exception")

            # Persist the updated scheduled_items statuses
            final_update: dict = {"scheduled_items": updated_items, "updated_at": now}

            # If all items are done (published or failed), update post-level status
            pending = [i for i in updated_items if i.get("status") == "scheduled"]
            if not pending and any_item_published:
                final_update["status"] = "posted"
                final_update["published_at"] = now

            await db.posts.update_one({"_id": post["_id"]}, {"$set": final_update})

        return {"published": published_count, "failed": failed_count}

    result = asyncio.run(_process())
    logger.info(f"Scheduled publish check complete: {result}")
    return result