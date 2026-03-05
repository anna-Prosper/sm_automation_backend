"""
Publishing API Routes
Handles instant publishing and scheduling of posts to social media platforms.
Supports per-platform and per-content-type publishing.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from bson import ObjectId
import logging

from app.db.session import get_database
from app.services.social_publisher import SocialPublisher
from app.utils.media import resolve_media_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/publishing", tags=["publishing"])


class PublishRequest(BaseModel):
    """Instant publish request"""
    platforms: List[str]  # ["whatsapp", "facebook", "instagram", "linkedin", "twitter"]
    whatsapp_recipients: Optional[List[str]] = None


class SinglePublishRequest(BaseModel):
    """Publish a single platform with a specific content type"""
    platform: str       # "instagram", "facebook", "linkedin", "twitter", "whatsapp"
    content_type: str   # "feed", "story", "carousel"


class ScheduleRequest(BaseModel):
    """Schedule a post for future publishing"""
    platforms: List[str]
    scheduled_at: datetime
    whatsapp_recipients: Optional[List[str]] = None


class SingleScheduleRequest(BaseModel):
    """Schedule single platform"""
    platform: str
    content_type: str  # "feed", "story", "carousel"
    scheduled_at: datetime


class PublishResult(BaseModel):
    """Result of a publish operation"""
    success: bool
    post_id: str
    results: Dict[str, Any]
    published_at: Optional[datetime] = None
    error: Optional[str] = None


def _get_public_image_url(post: dict) -> Optional[str]:
    """Get a publicly-accessible image URL from post data."""
    image_url = post.get("image_url") or post.get("final_image_path")
    if not image_url:
        return None
    if image_url.startswith("http"):
        return image_url
    return resolve_media_url(image_url, expires_in=86400)


def _build_full_caption(post: dict, platform: str, platform_data: dict = None) -> str:
    """Build the full post caption with hashtags for a given platform."""
    pd = platform_data or {}
    plat_content = pd.get(platform, {})
    caption = plat_content.get("caption") or post.get("caption", "")
    hashtags = plat_content.get("hashtags") or post.get("hashtags", [])
    if hashtags and platform in ("instagram", "facebook", "threads", "linkedin"):
        tag_str = " ".join(f"#{t}" for t in hashtags)
        caption = f"{caption}\n\n{tag_str}"
    return caption


VALID_PLATFORMS = {"whatsapp", "facebook", "instagram", "linkedin", "twitter"}
VALID_CONTENT_TYPES = {"feed", "story", "carousel"}



"""
Publishing API Routes
Handles instant publishing and scheduling of posts to social media platforms.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

from app.db.session import get_database
from app.services.social_publisher import SocialPublisher
from app.utils.media import resolve_media_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/publishing", tags=["publishing"])


class PublishRequest(BaseModel):
    platforms: List[str]
    whatsapp_recipients: Optional[List[str]] = None


class PublishResult(BaseModel):
    success: bool
    post_id: str
    results: Dict[str, Any]
    published_at: Optional[datetime] = None
    error: Optional[str] = None


def _get_public_image_url(post: dict) -> Optional[str]:
    """
    Return public/presigned image URL.
    Use long expiry because Meta/IG may fetch later.
    """
    image_url = post.get("image_url") or post.get("final_image_path")
    if not image_url:
        return None

    if image_url.startswith("http"):
        return image_url

    # 7 days
    return resolve_media_url(image_url, expires_in=604800)


def _build_full_caption(post: dict, platform: str, platform_data: dict = None) -> str:
    pd = platform_data or {}
    plat_content = pd.get(platform, {})

    caption = plat_content.get("caption") or post.get("caption", "")
    hashtags = plat_content.get("hashtags") or post.get("hashtags", [])

    if hashtags and platform in ("instagram", "facebook", "threads"):
        tag_str = " ".join(f"#{t}" for t in hashtags)
        caption = f"{caption}\n\n{tag_str}"

    return caption


@router.post("/{post_id}/publish", response_model=PublishResult)
async def publish_post_now(post_id: str, request: PublishRequest):
    db = await get_database()

    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    valid_platforms = {"whatsapp", "facebook", "instagram", "linkedin"}
    requested = set(request.platforms)
    invalid = requested - valid_platforms
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platforms: {', '.join(invalid)}. Supported: {', '.join(valid_platforms)}",
        )

    image_url = _get_public_image_url(post)

    platform_data = post.get("platforms", {})
    platform_captions = {p: _build_full_caption(post, p, platform_data) for p in request.platforms}

    carousel_urls = None
    if post.get("carousel_slides"):
        carousel_urls = []
        for slide in post["carousel_slides"]:
            url = slide.get("final_image_url") or slide.get("image_url")
            if url:
                if not url.startswith("http"):
                    url = resolve_media_url(url, expires_in=604800)
                carousel_urls.append(url)

    publisher = SocialPublisher()

    try:
        results = await publisher.publish_to_platforms(
            platforms=request.platforms,
            caption=post.get("caption", ""),
            image_url=image_url,
            hashtags=post.get("hashtags", []),
            carousel_image_urls=carousel_urls if carousel_urls and len(carousel_urls) >= 2 else None,
            platform_captions=platform_captions,
        )

        if request.whatsapp_recipients and "whatsapp" in request.platforms:
            results["whatsapp"] = await publisher.publish_whatsapp(
                caption=platform_captions.get("whatsapp", post.get("caption", "")),
                image_url=image_url,
                recipients=request.whatsapp_recipients,
            )

        return PublishResult(
            success=True,
            post_id=post_id,
            results=results,
            published_at=datetime.utcnow(),
        )

    except Exception as e:
        logger.exception(f"Publishing failed for post {post_id}: {e}")
        return PublishResult(
            success=False,
            post_id=post_id,
            results={},
            error=str(e),
        )



@router.post("/{post_id}/publish-single")
async def publish_single_platform(post_id: str, request: SinglePublishRequest):
    """
    Publish a single platform with a specific content type.
    E.g. publish only Instagram Story, or only LinkedIn feed post.
    """
    db = await get_database()

    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if request.platform not in VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {request.platform}")
    if request.content_type not in VALID_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid content type: {request.content_type}")

    # Get image URLs
    image_url = _get_public_image_url(post)
    story_image_url = None
    carousel_urls = None

    if request.content_type == "story":
        raw_story = post.get("story_image_url")
        if raw_story:
            story_image_url = raw_story if raw_story.startswith("http") else resolve_media_url(raw_story, expires_in=86400)
        else:
            story_image_url = image_url

    if request.content_type == "carousel" and post.get("carousel_slides"):
        carousel_urls = []
        for slide in post["carousel_slides"]:
            url = slide.get("final_image_url")
            if url:
                if not url.startswith("http"):
                    url = resolve_media_url(url, expires_in=86400)
                carousel_urls.append(url)

    # Build caption
    platform_data = post.get("platforms", {})
    caption = _build_full_caption(post, request.platform, platform_data)

    publisher = SocialPublisher()

    try:
        result = await publisher.publish_single(
            platform=request.platform,
            content_type=request.content_type,
            caption=caption,
            image_url=image_url,
            story_image_url=story_image_url,
            carousel_image_urls=carousel_urls,
        )
    except Exception as e:
        logger.exception(f"Single publish failed for {request.platform}/{request.content_type}")
        raise HTTPException(status_code=500, detail=str(e))

    now = datetime.utcnow()
    update_doc = {"updated_at": now}

    # Build the status key based on content_type and platform
    if result.get("success"):
        status_key = f"platforms.{request.platform}"
        if request.content_type == "story":
            status_key = f"story_publish_results.{request.platform}"
            update_doc["story_publish_results"] = post.get("story_publish_results", {})
            update_doc["story_publish_results"][request.platform] = result
        elif request.content_type == "carousel":
            status_key = f"carousel_publish_results.{request.platform}"
            update_doc["carousel_publish_results"] = post.get("carousel_publish_results", {})
            update_doc["carousel_publish_results"][request.platform] = result
        else:
            update_doc[f"platforms.{request.platform}.status"] = "published"
            update_doc[f"platforms.{request.platform}.published_at"] = now
            if result.get("platform_post_id"):
                update_doc[f"platforms.{request.platform}.platform_post_id"] = result["platform_post_id"]

        # Update publish_results
        existing_results = post.get("publish_results", {})
        result_key = f"{request.platform}_{request.content_type}" if request.content_type != "feed" else request.platform
        existing_results[result_key] = result
        update_doc["publish_results"] = existing_results

    await db.posts.update_one({"_id": oid}, {"$set": update_doc})

    return {
        "success": result.get("success", False),
        "post_id": post_id,
        "platform": request.platform,
        "content_type": request.content_type,
        "result": result,
        "published_at": now.isoformat() if result.get("success") else None,
    }


@router.post("/{post_id}/schedule-single")
async def schedule_single_platform(post_id: str, request: SingleScheduleRequest):
    """Schedule a single platform + content type for future publishing."""
    db = await get_database()

    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    now = datetime.now(timezone.utc)
    scheduled = request.scheduled_at
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    if scheduled <= now:
        raise HTTPException(status_code=400, detail="Scheduled time must be in the future")

    scheduled_items = post.get("scheduled_items", [])
    scheduled_items.append({
        "platform": request.platform,
        "content_type": request.content_type,
        "scheduled_at": request.scheduled_at,
        "status": "scheduled",
    })

    update_doc = {
        "scheduled_items": scheduled_items,
        "updated_at": now,
    }


    if post.get("status") not in ("posted",):
        update_doc["status"] = "scheduled"

    await db.posts.update_one({"_id": oid}, {"$set": update_doc})

    return {
        "success": True,
        "message": f"{request.platform} {request.content_type} scheduled for {request.scheduled_at.isoformat()}",
        "post_id": post_id,
        "platform": request.platform,
        "content_type": request.content_type,
        "scheduled_at": request.scheduled_at.isoformat(),
    }


@router.post("/{post_id}/schedule")
async def schedule_post(post_id: str, request: ScheduleRequest):
    """Schedule a post for future publishing at the specified time."""
    db = await get_database()

    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    now = datetime.now(timezone.utc)
    scheduled = request.scheduled_at
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    if scheduled <= now:
        raise HTTPException(status_code=400, detail="Scheduled time must be in the future")

    requested = set(request.platforms)
    invalid = requested - VALID_PLATFORMS
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported platforms: {', '.join(invalid)}")

    update_doc = {
        "status": "scheduled",
        "scheduled_at": request.scheduled_at,
        "scheduled_platforms": request.platforms,
        "updated_at": now,
    }

    if request.whatsapp_recipients:
        update_doc["whatsapp_recipients"] = request.whatsapp_recipients

    await db.posts.update_one({"_id": oid}, {"$set": update_doc})

    updated = await db.posts.find_one({"_id": oid})
    updated["_id"] = str(updated["_id"])

    return {
        "success": True,
        "message": f"Post scheduled for {request.scheduled_at.isoformat()}",
        "post_id": post_id,
        "scheduled_at": request.scheduled_at.isoformat(),
        "platforms": request.platforms,
        "status": "scheduled",
    }



@router.post("/{post_id}/unschedule")
async def unschedule_post(post_id: str):
    """Cancel a scheduled post and revert to approved/draft."""
    db = await get_database()

    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.get("status") != "scheduled":
        raise HTTPException(status_code=400, detail="Post is not scheduled")

    await db.posts.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "approved",
                "updated_at": datetime.utcnow(),
            },
            "$unset": {
                "scheduled_at": "",
                "scheduled_platforms": "",
                "scheduled_items": "",
            },
        },
    )

    return {"success": True, "message": "Schedule cancelled", "status": "approved"}


@router.get("/scheduled")
async def list_scheduled_posts():
    """List all currently scheduled posts."""
    db = await get_database()

    cursor = db.posts.find({"status": "scheduled"}).sort("scheduled_at", 1)
    posts = await cursor.to_list(length=100)

    for p in posts:
        p["_id"] = str(p["_id"])
        for k in ("image_url", "background_image_path", "final_image_path"):
            if p.get(k):
                p[k] = resolve_media_url(p[k], expires_in=7200)

    return {"posts": posts, "total": len(posts)}


@router.get("/linkedin/auth-url")
async def get_linkedin_auth_url(redirect_uri: str = "http://localhost:8000/api/publishing/linkedin/callback"):
    """
    Generate LinkedIn OAuth2 authorization URL.
    Frontend should redirect the user to this URL to authorize LinkedIn posting.
    """
    from app.core.config import settings

    if not settings.LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=400, detail="LINKEDIN_CLIENT_ID not configured")

    auth_url = (
        f"https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={settings.LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=openid%20profile%20w_member_social"
    )
    return {"auth_url": auth_url}


@router.get("/linkedin/callback")
async def linkedin_oauth_callback(code: str, redirect_uri: str = "http://localhost:8000/api/publishing/linkedin/callback"):
    """
    Handle LinkedIn OAuth2 callback. Exchange code for access token.
    """
    from app.core.config import settings
    import httpx

    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(token_url, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
        })
        data = resp.json()

    if "access_token" not in data:
        raise HTTPException(status_code=400, detail=f"Failed to get LinkedIn token: {data}")

    access_token = data["access_token"]

    # Get person URN
    async with httpx.AsyncClient(timeout=30) as client:
        profile_resp = await client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile = profile_resp.json()

    person_id = profile.get("sub", "")
    person_urn = f"urn:li:person:{person_id}" if person_id else ""

    return {
        "success": True,
        "access_token": access_token,
        "person_urn": person_urn,
        "message": "Add these to your .env file as LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN, then restart the API.",
    }


@router.get("/linkedin/whoami")
async def linkedin_whoami():
    """
    Fetch the correct LINKEDIN_PERSON_URN from the access token that is
    already set in the environment.

    Visit: GET /api/publishing/linkedin/whoami
    Copy the returned 'person_urn' value into your .env as LINKEDIN_PERSON_URN,
    then restart the API container.
    """
    from app.core.config import settings
    import httpx as _httpx

    token = settings.LINKEDIN_ACCESS_TOKEN
    if not token:
        raise HTTPException(status_code=400, detail="LINKEDIN_ACCESS_TOKEN is not set in .env")

    async with _httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"LinkedIn API error: {resp.text} — token may be expired or invalid",
            )
        profile = resp.json()

    person_id  = profile.get("sub", "")
    person_urn = f"urn:li:person:{person_id}" if person_id else ""
    name       = profile.get("name", "")
    email      = profile.get("email", "")

    current_urn = settings.LINKEDIN_PERSON_URN
    urn_correct = current_urn == person_urn

    return {
        "person_urn": person_urn,
        "name": name,
        "email": email,
        "current_env_urn": current_urn,
        "urn_is_correct": urn_correct,
        "action_needed": (
            None if urn_correct
            else f"Set LINKEDIN_PERSON_URN={person_urn} in your .env and restart the API."
        ),
    }



# Production Publishing

@router.post("/{post_id}/publish-production")
async def publish_to_production(post_id: str, body: dict):
    import os as _os
    from app.db.session import get_database
    from bson import ObjectId
    from datetime import datetime

    db = await get_database()
    post = await db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Post not found")

    prod_fb_token = _os.getenv("PROD_FACEBOOK_ACCESS_TOKEN", "")
    prod_ig_token = _os.getenv("PROD_INSTAGRAM_ACCESS_TOKEN", "")

    if not prod_fb_token and not prod_ig_token:
        return {"success": False, "error": "No production API keys configured"}

    from app.services.social_publisher import SocialPublisher
    publisher = SocialPublisher()

    if prod_fb_token:
        publisher.meta_access_token = prod_fb_token
        prod_fb_page = _os.getenv("PROD_FACEBOOK_PAGE_ID", "")
        if prod_fb_page:
            publisher.facebook_page_id = prod_fb_page
    if prod_ig_token:
        publisher.meta_access_token = prod_ig_token
        prod_ig_id = _os.getenv("PROD_INSTAGRAM_ACCOUNT_ID", "")
        if prod_ig_id:
            publisher.ig_account_id = prod_ig_id

    platforms = body.get("platforms", [])
    caption = post.get("caption", "")
    hashtags = post.get("hashtags", [])
    image_url = post.get("image_url") or post.get("final_image_path")

    if hashtags:
        caption += "\n\n" + " ".join(f"#{h}" for h in hashtags)

    results = {}
    for platform in platforms:
        try:
            result = await publisher.publish_single(
                platform=platform,
                caption=caption,
                image_url=image_url,
            )
            results[platform] = result
        except Exception as e:
            results[platform] = {"success": False, "error": str(e)}

    await db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": {
            "published_at": datetime.utcnow(),
            "status": "published",
            "production_publish_results": results,
        }}
    )

    return {"success": True, "results": results}
