"""
News Generation Routes
"""

from datetime import datetime, timedelta
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

logger = logging.getLogger(__name__)
from app.services.newsgen.pipeline import run_pipeline, run_search_pipeline

router = APIRouter()


def _format_release_time(value: str) -> str:
    if not value:
        return "Recent"

    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(cleaned)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        try:
            parsed = datetime.strptime(cleaned[:10], "%Y-%m-%d")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            return value


def _published_ts(value: str) -> float:
    if not value:
        return 0.0

    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        try:
            return datetime.strptime(cleaned[:10], "%Y-%m-%d").timestamp()
        except ValueError:
            return 0.0


def _tier_class(tier: str) -> str:
    if "NewsAPI" in tier:
        return "newsapi"
    if "Newsdata" in tier:
        return "newsdata"
    if "Currents" in tier:
        return "currents"
    return "none"


@router.post("/run")
async def run_news_generation():
    """
    Original pipeline endpoint
    Uses Event Registry fetching
    """
    try:
        result = await run_pipeline(top_n=10)
        return {
            "success": True,
            "posts_created": result.get("posts_created", 0),
            "posts_approved": result.get("posts_approved", 0),
            "message": "News generation pipeline completed successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score")
async def score_all_posts():
    """
    Score all draft posts
    """
    try:
        from app.services.newsgen.validation import score_all_drafts
        result = await score_all_drafts()
        return {
            "success": True,
            "posts_scored": result.get("posts_scored", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
async def generate_news():
    """
    Run the complete news generation pipeline with Event Registry fetching
    Returns detailed pipeline results
    """
    try:
        result = await run_pipeline(top_n=10)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview/fetch")
async def fetch_preview_articles():
    """
    Fetch fresh articles for the preview page.
    """
    try:
        from app.services.newsgen.multi_api_fetcher import Config, fetch_all_news

        articles = fetch_all_news()
        articles.sort(key=lambda a: _published_ts(a.get("published_date", "")), reverse=True)

        for article in articles:
            article["release_time"] = _format_release_time(article.get("published_date", ""))

        sources_with_results = len(set(a.get("source", "Unknown") for a in articles))
        api_count = len(set(a.get("api_tier", "unknown") for a in articles))
        max_count = max(
            (sum(1 for a in articles if a.get("source") == src["name"]) for src in Config.SOURCES),
            default=1,
        ) or 1

        source_stats = []
        for src in Config.SOURCES:
            src_articles = [a for a in articles if a.get("source") == src["name"]]
            count = len(src_articles)
            tier = src_articles[0].get("api_tier", "—") if src_articles else "—"
            source_stats.append(
                {
                    "name": src["name"],
                    "count": count,
                    "tier": tier,
                    "tier_class": _tier_class(tier),
                    "bar_pct": max(int((count / max_count) * 100), 8) if count > 0 else 0,
                }
            )

        source_names = [
            src["name"] for src in Config.SOURCES if any(a.get("source") == src["name"] for a in articles)
        ]

        now = datetime.utcnow()
        date_from = (now - timedelta(days=Config.LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")

        return {
            "articles": articles,
            "total_articles": len(articles),
            "total_sources": len(Config.SOURCES),
            "sources_with_results": sources_with_results,
            "api_count": api_count,
            "source_stats": source_stats,
            "source_names": source_names,
            "lookback_days": Config.LOOKBACK_DAYS,
            "date_from": date_from,
            "date_to": date_to,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_news(
    keywords: str = Query(..., description="Search keywords"),
    max_results: int = Query(20, ge=1, le=50)
):
    """
    Search for news with custom keywords using Event Registry
    """
    try:
        articles = await run_search_pipeline(keywords, max_results)
        
        if not articles:
            return {"articles": [], "count": 0}
        
        return {
            "articles": articles,
            "count": len(articles),
            "keywords": keywords
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sources")
async def get_sources():
    """
    Get list of available news sources
    """
    from app.services.newsgen.multi_api_fetcher import Config
    
    return {
        "sources": [
            {
                "name": s["name"],
                "domain": s["domain"]
            }
            for s in Config.SOURCES
        ],
        "total": len(Config.SOURCES)
    }


@router.get("/health")
async def health_check():
    """
    Check if required APIs are configured properly
    """
    from app.core.config import settings
    
    apis = {
        "eventregistry": bool(settings.NEWSAPI_KEY),
        "openai": bool(settings.OPENAI_API_KEY)
    }
    
    has_news_api = apis["eventregistry"]
    
    return {
        "status": "healthy" if has_news_api and apis["openai"] else "degraded",
        "apis": apis,
        "ready": has_news_api and apis["openai"]
    }


@router.get("/scheduler/status")
async def get_scheduler_status_endpoint():
    from app.scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/scheduler/trigger")
async def trigger_cycle_now(background_tasks: BackgroundTasks, review_minutes: int = Query(default=60, ge=1, le=120)):
    """Trigger a pipeline cycle in the background. Poll /scheduler/progress for status."""
    from app.services.newsgen.pipeline import run_scheduled_cycle
    from app.db.session import get_database

    # Reset progress
    db = await get_database()
    await db.pipeline_progress.update_one(
        {"_id": "current"},
        {"$set": {"step": 0, "total_steps": 9, "label": "Starting...", "detail": "", "status": "running", "updated_at": datetime.utcnow()}},
        upsert=True,
    )

    async def _run_and_schedule_autopost():
        """Run pipeline, then schedule delayed auto-post like the scheduler does."""
        from app.scheduler import scheduler, delayed_auto_post
        from apscheduler.triggers.date import DateTrigger
        from datetime import timezone

        result = await run_scheduled_cycle(review_minutes=review_minutes)

        # Schedule delayed auto-post
        post_time = datetime.now(timezone.utc) + timedelta(minutes=review_minutes)
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        try:
            scheduler.add_job(
                delayed_auto_post,
                DateTrigger(run_date=post_time),
                id=f"delayed_post_{cycle_id}",
                name=f"Delayed Auto-Post ({cycle_id})",
                replace_existing=True,
                kwargs={"cycle_id": cycle_id},
            )
            logger.info("Manual trigger: delayed auto-post scheduled for %s (%d min review)", post_time.strftime("%H:%M UTC"), review_minutes)
        except Exception as e:
            logger.warning("Failed to schedule delayed auto-post: %s (scheduler may not be running)", e)

        return result

    background_tasks.add_task(_run_and_schedule_autopost)
    return {"triggered": True, "review_minutes": review_minutes, "message": f"Pipeline started. Auto-post in {review_minutes} min. Poll /scheduler/progress for updates."}


@router.post("/scheduler/progress/reset")
async def reset_pipeline_progress():
    """Reset stuck progress state."""
    from app.db.session import get_database
    db = await get_database()
    await db.pipeline_progress.update_one(
        {"_id": "current"},
        {"$set": {"step": 0, "total_steps": 9, "label": "Idle", "detail": "", "status": "idle", "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    return {"ok": True, "message": "Progress reset"}


@router.get("/scheduler/progress")
async def get_pipeline_progress():
    """Get current pipeline progress for the running cycle."""
    from app.db.session import get_database
    from app.services.newsgen.pipeline import get_progress_cache
    db = await get_database()
    doc = await db.pipeline_progress.find_one({"_id": "current"})
    if not doc:
        return {"step": 0, "total_steps": 9, "label": "Idle", "detail": "", "status": "idle"}
    doc.pop("_id", None)
    # If we're on step 4 (scoring), overlay real-time cache from sync scorer
    cache = get_progress_cache()
    if doc.get("step") == 4 and doc.get("status") == "running" and cache.get("step") == 4:
        doc["detail"] = cache.get("detail", doc.get("detail", ""))
    return doc


@router.get("/feed")
async def get_news_feed(skip: int = 0, limit: int = 50, source: str = None):
    from app.db.session import get_database
    db = await get_database()
    query = {}
    if source:
        query["source"] = {"$regex": source, "$options": "i"}
    cursor = db.news_articles.find(query).sort("fetched_at", -1).skip(skip).limit(limit)
    articles = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        articles.append(doc)
    total = await db.news_articles.count_documents(query)
    return {"articles": articles, "total": total, "skip": skip, "limit": limit}
