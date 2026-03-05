from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from typing import Optional
import logging
import requests as _requests
from datetime import datetime

from app.services.newsgen.multi_api_fetcher import search_news_for_frontend, _resolve_newsapi_key, search_real_estate_for_frontend
from app.services.newsgen.pipeline import save_news_to_db

logger = logging.getLogger(__name__)
router = APIRouter()

EVENTREGISTRY_BASE = "https://eventregistry.org/api/v1"


class LookupUrlBody(BaseModel):
    url: str


def _lookup_article_by_url(article_url: str) -> Optional[dict]:
    """
    Two-step EventRegistry lookup:
    1. Map article URL → internal URI
    2. Fetch full article by URI
    Returns structured article dict or None if not found.
    """
    api_key = _resolve_newsapi_key()
    if not api_key:
        return None

    # Step 1: URL → URI
    try:
        mapper_resp = _requests.post(
            f"{EVENTREGISTRY_BASE}/articleMapper",
            json={"apiKey": api_key, "articleUrl": article_url},
            timeout=15,
        )
        mapper_resp.raise_for_status()
        mapper_data = mapper_resp.json()
    except Exception as e:
        logger.warning(f"[LookupUrl] articleMapper request failed: {e}")
        return None

    # Response is { "https://...": "uri_string_or_empty" }
    uri = None
    if isinstance(mapper_data, dict):
        for key, val in mapper_data.items():
            if val and str(val).strip():
                uri = str(val).strip()
                break

    if not uri:
        logger.info(f"[LookupUrl] No URI found for: {article_url}")
        return None

    logger.info(f"[LookupUrl] URI found: {uri}")

    # Step 2: URI → full article
    try:
        article_resp = _requests.post(
            f"{EVENTREGISTRY_BASE}/article/getArticle",
            json={
                "apiKey": api_key,
                "uri": uri,
                "includeArticleTitle": True,
                "includeArticleBody": True,
                "includeArticleImage": True,
                "includeSourceTitle": True,
                "includeArticlePublishingDate": True,
            },
            timeout=15,
        )
        article_resp.raise_for_status()
        article_data = article_resp.json()
    except Exception as e:
        logger.warning(f"[LookupUrl] getArticle request failed: {e}")
        return None

    # Response: { "<uri>": { "info": { ... } } }
    info = None
    if isinstance(article_data, dict):
        for key, val in article_data.items():
            if isinstance(val, dict):
                info = val.get("info") or val
                break

    if not info:
        logger.warning(f"[LookupUrl] Unexpected getArticle response structure")
        return None

    title = (info.get("title") or "").strip()
    body = (info.get("body") or info.get("summary") or "").strip()

    if not title:
        return None

    source_raw = info.get("source") or {}
    source_name = (
        source_raw.get("title") or source_raw.get("uri") or ""
    ).strip() if isinstance(source_raw, dict) else str(source_raw)

    image = info.get("image") or None

    date_raw = info.get("dateTimePub") or info.get("dateTime") or info.get("date") or ""
    date_display = date_raw[:10] if date_raw else ""

    return {
        "title": title,
        "content": body[:3000],
        "image": image,
        "url": article_url,
        "source": source_name,
        "date": date_display,
    }


@router.post("/lookup-url")
async def lookup_url(body: LookupUrlBody):
    """
    POST /api/news/lookup-url
    { url: "https://gulfnews.com/..." }
    Priority:
      1. Check own DB by source_url (instant, no API cost)
      2. Try EventRegistry articleMapper
      3. Return not found
    """
    url = (body.url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Priority 1: own database
    try:
        from app.db.session import get_database
        db = await get_database()
        post = await db.posts.find_one({"source_url": url})
        if post and post.get("title"):
            logger.info(f"[LookupUrl] Found in own DB: {url}")
            return {
                "found": True,
                "source": "db",
                "article": {
                    "title": post.get("title", ""),
                    "content": post.get("content", ""),
                    "image": post.get("image_url") or post.get("background_image_path") or None,
                    "url": url,
                    "source": post.get("source_name", ""),
                    "date": (post.get("published_date") or "")[:10],
                },
            }
    except Exception as e:
        logger.warning(f"[LookupUrl] DB lookup failed: {e}")

    # Priority 2: EventRegistry
    article = _lookup_article_by_url(url)
    if article:
        return {"found": True, "article": article, "source": "eventregistry"}

    return {"found": False}


@router.post("/upload-image")
async def upload_article_image(image: UploadFile = File(...)):
    """
    POST /api/news/upload-image
    Upload an article image, store to S3, return presigned URL.
    Used by manual post creation to attach an image before generating.
    """
    from app.services.newsgen.image_generator import RealEstateImageGenerator
    from app.services.newsgen.pipeline import _upload_image
    from app.utils.media import resolve_media_url

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    MAX_MB = 15
    if len(raw) > MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Image too large (>{MAX_MB}MB)")

    helper = RealEstateImageGenerator()
    jpeg_bytes = helper._to_jpeg_bytes(raw)
    if not jpeg_bytes:
        raise HTTPException(status_code=400, detail="Unsupported image format")

    filename = f"article_img_{datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}.jpg"
    stored_url = await _upload_image(jpeg_bytes, f"uploads/{filename}")

    if not stored_url:
        raise HTTPException(status_code=500, detail="Image upload failed")

    presigned = resolve_media_url(stored_url, expires_in=7200)
    return {"url": presigned, "raw_url": stored_url}


class NewsSearchBody(BaseModel):
    keywords: str
    max_results: int = 10
    days_back: int = 30
    save_to_db: bool = False


@router.post("/search")
async def search_news(body: NewsSearchBody):
    """
    POST /api/news/search
    { keywords, max_results, days_back }
    """
    keywords = (body.keywords or "").strip()
    if len(keywords) < 2:
        raise HTTPException(status_code=400, detail="keywords must be at least 2 characters")

    logger.info(f"News search: '{keywords}', max={body.max_results}, days_back={body.days_back}")

    articles = search_news_for_frontend(
        keywords=keywords,
        max_results=body.max_results,
        days_back=body.days_back,
    )

    if body.save_to_db:
        await save_news_to_db(articles, source="search")

    return {"articles": articles, "count": len(articles), "keywords": keywords}


@router.get("/trending")
async def trending(
    days_back: int = Query(7, ge=1, le=60),
    max_results: int = Query(20, ge=1, le=50),
):
    """
    GET /api/news/trending?days_back=7&max_results=20
    """
    keywords = "Dubai real estate"
    articles = search_news_for_frontend(
        keywords=keywords,
        max_results=max_results,
        days_back=days_back,
    )
    return {"articles": articles, "count": len(articles), "keywords": keywords, "days_back": days_back}


@router.get("/real-estate")
async def real_estate_news(
    days_back: int = Query(7, ge=1, le=60),
    max_results: int = Query(20, ge=1, le=50),
):
    """
    GET /api/news/real-estate?days_back=7&max_results=20
    Returns only articles classified as real-estate by EventRegistry
    (concept URI filter, not keyword search).
    """
    articles = search_real_estate_for_frontend(
        days_back=days_back,
        max_results=max_results,
    )
    return {"articles": articles, "count": len(articles), "days_back": days_back}


@router.post("/refetch")
async def refetch_trending(
    days_back: int = Query(7, ge=1, le=60),
    max_results: int = Query(40, ge=1, le=80),
):
    """
    Refetch trending news and UPSERT into database.
    Frontend can call this repeatedly to refresh.
    """
    keywords = "Dubai real estate"
    articles = search_news_for_frontend(
        keywords=keywords,
        max_results=max_results,
        days_back=days_back,
    )
    saved = await save_news_to_db(articles, source="trending")
    return {
        "ok": True,
        "saved": saved,
        "count": len(articles),
        "keywords": keywords,
        "days_back": days_back,
        "articles": articles,
    }


@router.get("/search/location")
async def search_location(location: str, max_results: int = Query(10, ge=1, le=50)):
    keywords = f"{location} real estate Dubai"
    articles = search_news_for_frontend(keywords=keywords, max_results=max_results, days_back=30)
    return {"articles": articles, "count": len(articles), "location": location}


@router.get("/search/developer")
async def search_developer(developer: str, max_results: int = Query(10, ge=1, le=50)):
    keywords = f"{developer} Dubai real estate"
    articles = search_news_for_frontend(keywords=keywords, max_results=max_results, days_back=30)
    return {"articles": articles, "count": len(articles), "developer": developer}


@router.get("/health")
async def news_health():
    return {"ok": True, "service": "news"}
