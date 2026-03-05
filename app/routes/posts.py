"""
Posts API Routes
image regeneration and editing features
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List, Optional
from datetime import datetime
import logging
from io import BytesIO

from app.db.models import (
    Post, PostUpdate, PostStatus, ImageRegenerateRequest,
    BackgroundProvider, PlatformUpdate, FormatsGenerated
)
from app.db.session import get_database
from app.services.templates import select_and_render, TemplateInputs
from app.services.templates.base_template import BaseTemplate
from app.services.templates.template_selector import select_and_render_bytes
from app.utils.media import resolve_media_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/posts", tags=["posts"])


def _sanitize_doc(doc: dict) -> dict:
    """
    """
    from bson import ObjectId
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _sanitize_doc(v)
        elif isinstance(v, list):
            out[k] = [str(i) if isinstance(i, ObjectId) else i for i in v]
        else:
            out[k] = v

    if "_id" in out:
        out["id"] = out["_id"]
    return out


@router.get("/", response_model=List[Post])
async def list_posts(
    status: Optional[PostStatus] = None,
    post_type: Optional[str] = None,
    limit: int = 50,
    skip: int = 0
):
    """
    List all posts with optional filters
    
    Query params:
    - status: Filter by status (draft/approved/published)
    - post_type: Filter by type (complete/basic/manual)
    - limit: Max results (default: 50)
    - skip: Pagination offset (default: 0)
    """
    db = await get_database()
    
    # Build filter
    filter_query = {}
    if status:
        filter_query["status"] = status.value
    if post_type:
        filter_query["post_type"] = post_type
    
    # Query database
    cursor = db.posts.find(filter_query).sort("created_at", -1).skip(skip).limit(limit)
    posts = await cursor.to_list(length=limit)
    return [_sanitize_doc(p) for p in posts]


@router.get("/{post_id}", response_model=Post)
async def get_post(post_id: str):
    """Get single post by ID"""
    db = await get_database()
    
    from bson import ObjectId
    try:
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    return _sanitize_doc(post)



@router.put("/{post_id}", response_model=Post)
async def update_post(post_id: str, update_data: PostUpdate):
    """
    Update post fields
    
    Can update:
    - headline, caption, hashtags
    - green_words, gold_words (for highlighting)
    - status, category
    - scheduled_at
    """
    db = await get_database()
    
    from bson import ObjectId
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    # Build update document
    update_doc = {
        "updated_at": datetime.utcnow()
    }
    
    for field, value in update_data.dict(exclude_unset=True).items():
        if value is not None:
            update_doc[field] = value
    
    # Update database
    result = await db.posts.update_one(
        {"_id": object_id},
        {"$set": update_doc}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Return updated post
    post = await db.posts.find_one({"_id": object_id})
    return _sanitize_doc(post)



@router.delete("/{post_id}")
async def delete_post(post_id: str):
    """Delete post by ID"""
    db = await get_database()
    
    from bson import ObjectId
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    result = await db.posts.delete_one({"_id": object_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")
    
    return {"success": True, "message": "Post deleted"}



@router.post("/{post_id}/approve")
async def approve_post(post_id: str):
    """Approve post for publishing"""
    db = await get_database()
    
    from bson import ObjectId
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    result = await db.posts.update_one(
        {"_id": object_id},
        {
            "$set": {
                "status": "approved",
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")
    
    return {"success": True, "message": "Post approved"}


@router.post("/{post_id}/regenerate")
async def regenerate_image(post_id: str, request: ImageRegenerateRequest):
    """
    Regenerate post image

    Options:
    1. AI generation (stability_ai) - requires ai_prompt
    2. Stock photos (pexels, unsplash) - requires stock_query
    3. Upload (uploaded) - requires uploaded_image_url
    4. Gradient (gradient) - no requirements

    Also supports:
    - Updating gold_words for highlighting
    - Custom headline override
    """
    db = await get_database()

    from bson import ObjectId
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    # Get existing post
    post = await db.posts.find_one({"_id": object_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    logger.info(f"Regenerating image for post {post_id} with {request.background_type}")

    # STEP 1: GENERATE NEW BACKGROUND
    background_bytes = None
    background_provider = request.background_type
    background_ref = None
    ai_prompt = None

    try:
        if request.background_type == BackgroundProvider.STABILITY_AI:
            if not request.ai_prompt:
                raise HTTPException(status_code=400, detail="ai_prompt required for AI generation")

            from app.services.newsgen.image_generator import RealEstateImageGenerator
            bg_gen = RealEstateImageGenerator()

            article = {
                "title": request.ai_prompt,
                "content": post.get("content", ""),
            }

            result = bg_gen.generate_post_image(
                article, style="luxury", platform="instagram_square", add_branding=False,
            )

            if result and result.get("image_bytes"):
                background_bytes = result["image_bytes"]
                ai_prompt = request.ai_prompt
            else:
                raise Exception("AI generation returned no image")

        elif request.background_type in (BackgroundProvider.PEXELS, BackgroundProvider.UNSPLASH):
            if not request.stock_query:
                raise HTTPException(status_code=400, detail="stock_query required for stock photos")

            from app.services.newsgen.image_generator import RealEstateImageGenerator
            bg_gen = RealEstateImageGenerator()

            article = {"title": request.stock_query, "content": ""}
            result = bg_gen.generate_post_image(
                article, style="editorial", platform="instagram_square", add_branding=False,
            )

            if result and result.get("image_bytes"):
                background_bytes = result["image_bytes"]
            else:
                raise Exception("Stock photo generation failed")

        elif request.background_type == BackgroundProvider.UPLOADED:
            if not request.uploaded_image_url:
                raise HTTPException(status_code=400, detail="uploaded_image_url required for upload")
            from app.services.newsgen.image_generator import RealEstateImageGenerator
            _dl = RealEstateImageGenerator()
            background_bytes = _dl._download_image(request.uploaded_image_url)
            if not background_bytes:
                raise HTTPException(status_code=400, detail="Failed to download image from URL")

        elif request.background_type == BackgroundProvider.GRADIENT:
            from app.services.newsgen.image_generator import SimpleBackgroundGenerator
            import io as _io
            bg_gen = SimpleBackgroundGenerator()
            img = bg_gen.generate_gradient_background(1080, 1080, "elegant")
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            background_bytes = buf.getvalue()

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported background type: {request.background_type}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Background generation error: {e}")
        raise HTTPException(status_code=500, detail=f"Background generation failed: {str(e)}")

    # STEP 2: RENDER NEW POSTER
    poster_bytes = None
    template_name = None

    try:
        from app.services.render.binayah_renderer import parse_keywords_string

        headline = request.headline_override or post.get("headline") or post.get("title", "")

        db_gold = post.get("gold_words", "")
        if isinstance(db_gold, list):
            db_gold = ",".join(db_gold)

        gold_words = parse_keywords_string(request.gold_words or db_gold)
        red_words = set()

        # Auto-extract if empty
        if not gold_words:
            gold_words = BaseTemplate.extract_gold_words(headline)
        if not red_words:
            red_words = BaseTemplate.extract_red_words(headline)

        # Determine sentiment
        sentiment = "neutral"
        if any(w in headline.lower() for w in ["crash", "drop", "fall", "decline"]):
            sentiment = "negative"
        elif any(w in headline.lower() for w in ["surge", "growth", "rise", "launch"]):
            sentiment = "positive"

        template_inputs = TemplateInputs(
            headline=headline,
            website_url="binayah.com",
            gold_words=gold_words,
            red_words=red_words,
            background_image_bytes=background_bytes,
        )

        poster_bytes, template_name = select_and_render_bytes(
            inputs=template_inputs,
            sentiment=sentiment,
        )

        logger.info(f"Poster rendered with template: {template_name}")

    except Exception as e:
        logger.error(f"Poster rendering error: {e}")
        raise HTTPException(status_code=500, detail=f"Poster rendering failed: {str(e)}")

    # STEP 3: UPLOAD IMAGES
    from app.services.newsgen.pipeline import _upload_image

    background_url = None
    if background_bytes:
        bg_filename = f"bg_{post_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        background_url = await _upload_image(background_bytes, bg_filename)

    poster_url = None
    if poster_bytes:
        poster_filename = f"poster_{post_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        poster_url = await _upload_image(poster_bytes, poster_filename)

    # STEP 4: UPDATE DATABASE
    update_doc = {
        "background_image_path": background_url,
        "final_image_path": poster_url,
        "image_url": poster_url,
        "background_provider": background_provider.value,
        "background_ref": background_ref,
        "template_id": template_name,
        "updated_at": datetime.utcnow(),
    }

    if request.gold_words:
        update_doc["gold_words"] = request.gold_words
    if request.headline_override:
        update_doc["headline"] = request.headline_override
    if ai_prompt:
        update_doc["ai_prompt"] = ai_prompt

    await db.posts.update_one({"_id": object_id}, {"$set": update_doc})

    updated_post = await db.posts.find_one({"_id": object_id})
    return _sanitize_doc(updated_post)


# UPLOAD CUSTOM IMAGE

@router.post("/{post_id}/upload-image")
async def upload_custom_image(
    post_id: str,
    image: UploadFile | None = File(default=None),
    image_url: str | None = Form(default=None),
    green_words: Optional[str] = Form(None),
    gold_words: Optional[str] = Form(None),
    headline_override: Optional[str] = Form(None),
):
    """
    Upload custom background image (file OR image_url) and regenerate poster.

    Form data:
    - image: Image file (optional)
    - image_url: Direct image URL (optional)
    - green_words, gold_words: optional keywords
    - headline_override: optional headline
    """
    db = await get_database()

    from bson import ObjectId
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    post = await db.posts.find_one({"_id": object_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if not image and not image_url:
        raise HTTPException(status_code=400, detail="Provide image file or image_url")

    import io
    from PIL import Image
    from app.utils.media import resolve_media_url
    from app.services.newsgen.image_generator import RealEstateImageGenerator

    _img_helper = RealEstateImageGenerator()
    MAX_MB = 15

    # Get background bytes (from file OR url)
    if image:
        ct = (image.content_type or "").lower()
        raw_bytes = await image.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Uploaded image is empty")
        if len(raw_bytes) > MAX_MB * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Image too large (> {MAX_MB}MB)")
    else:
        raw_bytes = _img_helper._download_image(image_url or "")
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Failed to download image from URL")

    # Convert to JPEG using the full fallback chain (handles AVIF, HEIC, WebP, etc.)
    image_bytes = _img_helper._to_jpeg_bytes(raw_bytes)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Unsupported image format — could not convert to JPEG")

    # Render new poster with your template system
    try:
        from app.services.render.binayah_renderer import parse_keywords_string

        headline = headline_override or post.get("headline") or post.get("title", "")

        db_gold = post.get("gold_words", "")
        if isinstance(db_gold, list):
            db_gold = ",".join(db_gold)

        gold_set = parse_keywords_string(gold_words or db_gold)
        if not gold_set:
            gold_set = BaseTemplate.extract_gold_words(headline)

        red_set = BaseTemplate.extract_red_words(headline)
        sentiment = "negative" if red_set else "neutral"

        inputs = TemplateInputs(
            headline=headline,
            website_url="binayah.com",
            gold_words=gold_set,
            red_words=red_set,
            background_image_bytes=image_bytes,
        )

        poster_path, template_name = select_and_render(
            inputs=inputs,
            sentiment=sentiment,
            output_dir="storage/images",
        )

        with open(poster_path, "rb") as f:
            poster_raw = f.read()

        poster_bytes = _img_helper._to_jpeg_bytes(poster_raw)

    except Exception as e:
        logger.error(f"Poster rendering error: {e}")
        raise HTTPException(status_code=500, detail=f"Poster rendering failed: {str(e)}")

    #  Upload to S3
    from app.services.newsgen.pipeline import _upload_image

    bg_filename = f"custom_bg_{post_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
    background_url = await _upload_image(image_bytes, bg_filename)

    poster_filename = f"poster_{post_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
    poster_url = await _upload_image(poster_bytes, poster_filename)

    # Update database
    update_doc = {
        "background_image_path": background_url,
        "final_image_path": poster_url,
        "image_url": poster_url,
        "background_provider": "uploaded" if image else "url",
        "updated_at": datetime.utcnow(),
    }

    if green_words:
        update_doc["green_words"] = green_words
    if gold_words:
        update_doc["gold_words"] = gold_words
    if headline_override:
        update_doc["headline"] = headline_override

    await db.posts.update_one({"_id": object_id}, {"$set": update_doc})

    updated_post = await db.posts.find_one({"_id": object_id})
    updated_post = _sanitize_doc(updated_post)

    for k in ("image_url", "background_image_path", "final_image_path"):
        if updated_post.get(k):
            updated_post[k] = resolve_media_url(updated_post[k], expires_in=7200)

    return updated_post



@router.get("/{post_id}/preview")
async def preview_image(post_id: str):
    """Get post image as streaming response (for preview)"""
    db = await get_database()
    
    from bson import ObjectId
    try:
        object_id = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    post = await db.posts.find_one({"_id": object_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    image_url = post.get('image_url') or post.get('final_image_path')
    
    if not image_url:
        raise HTTPException(status_code=404, detail="No image available")
    
    # If S3 URL, redirect
    if image_url.startswith('http'):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=image_url)
    
    # If local path, stream file
    import os
    if os.path.exists(image_url):
        def iter_file():
            with open(image_url, 'rb') as f:
                yield from f
        
        return StreamingResponse(iter_file(), media_type="image/png")
    
    raise HTTPException(status_code=404, detail="Image file not found")


async def _get_post_or_404(post_id: str, db):
    """Fetch post by ID or raise 404. Returns raw mongo doc."""
    from bson import ObjectId
    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post, oid


def _article_from_post(post: dict) -> dict:
    """Build a minimal article dict from a stored post document."""
    return {
        "title": post.get("headline") or post.get("title", ""),
        "content": post.get("content", ""),
        "source": post.get("source_name", ""),
        "url": post.get("source_url", ""),
        "article_image_url": post.get("background_image_path"),
    }


def _presign_post_urls(doc: dict, expires: int = 7200) -> dict:
    """Presign all S3 URLs in a post document (feed + story + carousel)."""
    # Feed image URLs
    for k in ("image_url", "background_image_path", "final_image_path"):
        if doc.get(k):
            doc[k] = resolve_media_url(doc[k], expires_in=expires)

    # Story image URL
    if doc.get("story_image_url"):
        doc["story_image_url"] = resolve_media_url(doc["story_image_url"], expires_in=expires)

    # Carousel slide URLs
    slides = doc.get("carousel_slides")
    if slides and isinstance(slides, list):
        for slide in slides:
            if isinstance(slide, dict):
                for k in ("final_image_url", "background_image_url"):
                    if slide.get(k):
                        slide[k] = resolve_media_url(slide[k], expires_in=expires)

    return doc



@router.get("/{post_id}/platforms")
async def get_platforms(post_id: str):
    """
    Return all per-platform content for a post.

    Response: { instagram: {...}, twitter: {...}, ... }
    Also includes recommendation to generate all formats if confidence is high.
    """
    db = await get_database()
    post, _ = await _get_post_or_404(post_id, db)

    platforms = post.get("platforms") or {}
    confidence = post.get("confidence_score") or 0
    relevance = post.get("relevance_score") or 0

    # Presign story image URL
    story_url = post.get("story_image_url")
    if story_url:
        story_url = resolve_media_url(story_url, expires_in=7200)

    # Presign carousel slide URLs
    carousel_slides = post.get("carousel_slides") or []
    for slide in carousel_slides:
        if isinstance(slide, dict):
            for k in ("final_image_url", "background_image_url"):
                if slide.get(k):
                    slide[k] = resolve_media_url(slide[k], expires_in=7200)

    return {
        "post_id": post_id,
        "platforms": platforms,
        "formats_generated": post.get("formats_generated") or {"feed": False, "story": False, "carousel": False},
        "story_image_url": story_url,
        "story_status": post.get("story_status", "draft"),
        "carousel_slides": carousel_slides,
        "carousel_status": post.get("carousel_status", "draft"),
        "recommend_generate_all": confidence >= 85 and relevance >= 80,
    }


@router.patch("/{post_id}/platforms/{platform}")
async def update_platform(post_id: str, platform: str, update: PlatformUpdate):
    """
    Update caption/hashtags/scheduled_at for a single platform.

    Allowed platforms: instagram, facebook, twitter, whatsapp, threads
    """
    VALID_PLATFORMS = {"instagram", "facebook", "twitter", "whatsapp", "threads"}
    if platform not in VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Invalid platform. Must be one of: {', '.join(VALID_PLATFORMS)}")

    db = await get_database()
    post, oid = await _get_post_or_404(post_id, db)

    # Build per-field update
    set_fields = {"updated_at": datetime.utcnow()}

    if update.caption is not None:
        set_fields[f"platforms.{platform}.caption"] = update.caption
    if update.hashtags is not None:
        set_fields[f"platforms.{platform}.hashtags"] = update.hashtags
    if update.image_url is not None:
        set_fields[f"platforms.{platform}.image_url"] = update.image_url
    if update.scheduled_at is not None:
        set_fields[f"platforms.{platform}.scheduled_at"] = update.scheduled_at

    await db.posts.update_one({"_id": oid}, {"$set": set_fields})
    post = await db.posts.find_one({"_id": oid})
    return _sanitize_doc(post)


@router.post("/{post_id}/platforms/{platform}/approve")
async def approve_platform(post_id: str, platform: str):
    """
    Approve a single platform's content for publishing.
    Sets platforms.{platform}.status = 'approved'.
    """
    VALID_PLATFORMS = {"instagram", "facebook", "twitter", "whatsapp", "threads"}
    if platform not in VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Invalid platform: {platform}")

    db = await get_database()
    post, oid = await _get_post_or_404(post_id, db)

    platforms = post.get("platforms") or {}
    if platform not in platforms:
        raise HTTPException(status_code=404, detail=f"No content generated for platform: {platform}")

    await db.posts.update_one(
        {"_id": oid},
        {"$set": {
            f"platforms.{platform}.status": "approved",
            "status": "approved",
            "approved_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }}
    )

    return {"success": True, "post_id": post_id, "platform": platform, "status": "approved"}



@router.post("/{post_id}/generate-platforms")
async def generate_platforms(post_id: str):
    """
    Generate per-platform content for a post that was created manually
    (e.g. via Create from Article) and skipped the pipeline.
    Calls MultiPlatformPostCreator to produce captions for all 5 platforms.
    """
    db = await get_database()
    post, oid = await _get_post_or_404(post_id, db)

    article = _article_from_post(post)

    from app.services.newsgen.post_creator import MultiPlatformPostCreator
    multi_creator = MultiPlatformPostCreator()

    try:
        platforms_content = multi_creator.create_all_platforms(article)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Platform generation failed: {e}")

    # Persist to DB
    await db.posts.update_one(
        {"_id": oid},
        {"$set": {"platforms": platforms_content, "updated_at": datetime.utcnow()}}
    )

    post = await db.posts.find_one({"_id": oid})
    return {
        **_sanitize_doc(post),
        "platforms": platforms_content,
    }


@router.post("/{post_id}/generate-story")
async def generate_story(post_id: str):
    """
    Generate 9:16 story image for an existing post.

    Flow:
      1. Generate background with Stability AI (instagram_story = 1080×1920)
      2. Render with StoryTemplate
      3. Upload to S3
      4. Update post: story_image_url, formats_generated.story = True
    """
    db = await get_database()
    post, oid = await _get_post_or_404(post_id, db)

    article = _article_from_post(post)
    headline = post.get("headline") or post.get("title", "")

    from app.services.newsgen.image_generator import RealEstateImageGenerator, SimpleBackgroundGenerator
    from app.services.templates.story_template import StoryTemplate
    from app.services.templates.base_template import TemplateInputs, BaseTemplate
    from app.services.newsgen.pipeline import _upload_image
    import io as _io

    image_gen = RealEstateImageGenerator()

    # Step 1: Background
    background_bytes = None
    ai_prompt = None

    try:
        result = image_gen.generate_story_image(article, style="luxury", platform="instagram_story")
        if result and result.get("image_bytes"):
            background_bytes = result["image_bytes"]
            ai_prompt = result.get("prompt", "")
            logger.info(f"Story background generated for post {post_id}")
    except Exception as e:
        logger.warning(f"Story Stability AI failed: {e} — using gradient fallback")

    if not background_bytes:
        bg_gen = SimpleBackgroundGenerator()
        img = bg_gen.generate_gradient_background(1080, 1920, "elegant")
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        background_bytes = buf.getvalue()

    # Step 2: Render
    gold_words = BaseTemplate.extract_gold_words(headline)
    red_words = BaseTemplate.extract_red_words(headline)

    inputs = TemplateInputs(
        headline=headline,
        website_url="binayah.com",
        gold_words=gold_words,
        red_words=red_words,
        background_image_bytes=background_bytes,
    )

    try:
        story_bytes = StoryTemplate().render_to_bytes(inputs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Story rendering failed: {e}")

    # Step 3: Upload
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bg_url = await _upload_image(background_bytes, f"story_bg_{post_id}_{ts}.png")
    story_url = await _upload_image(story_bytes, f"story_{post_id}_{ts}.png")

    if not story_url:
        raise HTTPException(status_code=500, detail="Story image upload failed")

    # Step 4: Update DB
    await db.posts.update_one(
        {"_id": oid},
        {"$set": {
            "story_image_url": story_url,
            "formats_generated.story": True,
            "updated_at": datetime.utcnow(),
        }}
    )
    if ai_prompt:
        await db.posts.update_one({"_id": oid}, {"$set": {"story_ai_prompt": ai_prompt}})

    post = await db.posts.find_one({"_id": oid})
    presigned_story_url = resolve_media_url(story_url, expires_in=7200)
    return {
        **_sanitize_doc(post),
        "generated": "story",
        "story_image_url": presigned_story_url,
    }


@router.post("/{post_id}/generate-carousel")
async def generate_carousel(post_id: str, num_slides: Optional[int] = None):
    """
    Generate a carousel for an existing post.

    Query param:
        num_slides: If omitted or None, the AI decides the slide count (3–8).
                    If provided (e.g. ?num_slides=5), exactly that many slides are generated.

    Flow:
      1. create_carousel_angles(num_slides) → N content angles
      2. generate_carousel_slides() → N Stability AI backgrounds
      3. CarouselSlideTemplate.render_slide() × N
      4. Upload all images to S3
      5. Update post: carousel_slides[], formats_generated.carousel = True
    """
    db = await get_database()
    post, oid = await _get_post_or_404(post_id, db)

    # Clamp to safe range
    if num_slides is not None:
        num_slides = max(2, min(10, num_slides))

    article = _article_from_post(post)

    from app.services.newsgen.post_creator import MultiPlatformPostCreator
    from app.services.newsgen.image_generator import RealEstateImageGenerator, SimpleBackgroundGenerator
    from app.services.templates.carousel_slide_template import CarouselSlideTemplate
    from app.services.templates.base_template import TemplateInputs, BaseTemplate
    from app.services.newsgen.pipeline import _upload_image
    import io as _io

    multi_creator = MultiPlatformPostCreator()
    image_gen = RealEstateImageGenerator()
    carousel_template = CarouselSlideTemplate()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # Step 1: Generate angles
    try:
        angles = multi_creator.create_carousel_angles(article, num_slides=num_slides)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Carousel angle generation failed: {e}")

    actual_n = len(angles)

    # Step 2: Generate backgrounds
    try:
        slide_images = image_gen.generate_carousel_slides(article, angles, style="luxury")
    except Exception as e:
        logger.warning(f"Carousel Stability AI failed: {e} — using gradient fallback")
        slide_images = []

    # Pad with gradient fallback if needed
    while len(slide_images) < actual_n:
        idx = len(slide_images)
        angle = angles[idx] if idx < len(angles) else {"slide_number": idx+1, "headline": "", "slide_caption": "", "angle_label": ""}
        bg_gen = SimpleBackgroundGenerator()
        img = bg_gen.generate_gradient_background(1080, 1350, "elegant")
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        slide_images.append({**angle, "image_bytes": buf.getvalue()})

    # Step 3 + 4: Render each slide and upload
    carousel_slides = []

    for slide_data in slide_images[:actual_n]:
        slide_num = slide_data.get("slide_number", len(carousel_slides) + 1)
        headline = slide_data.get("headline", "")
        slide_caption = slide_data.get("slide_caption", "")
        bg_bytes = slide_data.get("image_bytes")

        gold_words = BaseTemplate.extract_gold_words(headline)
        red_words = BaseTemplate.extract_red_words(headline)

        inputs = TemplateInputs(
            headline=headline or post.get("headline", ""),
            website_url="binayah.com",
            gold_words=gold_words,
            red_words=red_words,
            background_image_bytes=bg_bytes,
        )

        try:
            rendered_bytes = carousel_template.render_slide(
                inputs,
                slide_number=slide_num,
                total_slides=actual_n,
                slide_caption=slide_caption,
            )
        except Exception as e:
            logger.error(f"Slide {slide_num} render failed: {e}")
            rendered_bytes = bg_bytes

        bg_url    = await _upload_image(bg_bytes,       f"carousel_bg_{post_id}_s{slide_num}_{ts}.png") if bg_bytes else None
        final_url = await _upload_image(rendered_bytes, f"carousel_s{slide_num}_{post_id}_{ts}.png")    if rendered_bytes else None

        carousel_slides.append({
            "slide_number": slide_num,
            "headline": headline,
            "slide_caption": slide_caption,
            "angle_label": slide_data.get("angle_label", ""),
            "background_image_url": bg_url,
            "final_image_url": final_url,
            "ai_prompt": slide_data.get("prompt", "") or slide_data.get("ai_prompt", ""),
        })

    # Step 5: Update DB
    await db.posts.update_one(
        {"_id": oid},
        {"$set": {
            "carousel_slides": carousel_slides,
            "formats_generated.carousel": True,
            "updated_at": datetime.utcnow(),
        }}
    )

    # Presign carousel slide URLs for frontend
    presigned_slides = []
    for slide in carousel_slides:
        ps = dict(slide)
        for k in ("final_image_url", "background_image_url"):
            if ps.get(k):
                ps[k] = resolve_media_url(ps[k], expires_in=7200)
        presigned_slides.append(ps)

    post = await db.posts.find_one({"_id": oid})
    return {
        **_sanitize_doc(post),
        "generated": "carousel",
        "carousel_slides": presigned_slides,
    }


@router.post("/{post_id}/generate-all")
async def generate_all_formats(post_id: str):
    """
    Generate story + carousel for an existing post in one call.
    Runs story first, then carousel. Returns full updated post with all formats.

    Recommended when confidence_score >= 85 and relevance_score >= 80.
    """
    db = await get_database()
    post, oid = await _get_post_or_404(post_id, db)

    results = {
        "post_id": post_id,
        "generated": [],
        "errors": [],
    }

    # Generate story
    try:
        story_response = await generate_story(post_id)
        results["story_image_url"] = story_response.get("story_image_url")
        results["generated"].append("story")
    except HTTPException as e:
        results["errors"].append({"format": "story", "error": e.detail})
    except Exception as e:
        results["errors"].append({"format": "story", "error": str(e)})

    # Generate carousel
    try:
        carousel_response = await generate_carousel(post_id)
        results["carousel_slides"] = carousel_response.get("carousel_slides", [])
        results["generated"].append("carousel")
    except HTTPException as e:
        results["errors"].append({"format": "carousel", "error": e.detail})
    except Exception as e:
        results["errors"].append({"format": "carousel", "error": str(e)})

    # Return final post state
    post = await db.posts.find_one({"_id": oid})
    result_doc = {
        **_sanitize_doc(post),
        **results,
    }
    # Presign all URLs in the final response
    _presign_post_urls(result_doc, expires=7200)
    return result_doc



@router.patch("/posts/{post_id}/edit-overlay-text")
async def edit_overlay_text(post_id: str, body: dict):
    """
    Edit the headline text that gets overlaid on the generated image.
    After editing, re-renders the poster with the new text.
    Body: { headline: str, gold_words: str?, red_words: str? }
    """
    from bson import ObjectId
    from datetime import datetime
    from app.db.session import get_database

    db = await get_database()
    post = await db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Post not found")

    new_headline = body.get("headline", post.get("headline", ""))
    new_gold = body.get("gold_words", post.get("gold_words", ""))
    new_red = body.get("red_words", post.get("red_words", ""))

    update_fields = {
        "headline": new_headline,
        "gold_words": new_gold,
        "red_words": new_red,
        "updated_at": datetime.utcnow(),
    }

    # Try to re-render with template if background exists
    try:
        from app.services.templates import select_and_render, TemplateInputs
        from app.services.templates.base_template import BaseTemplate

        background_url = post.get("background_image_path")
        template_id = post.get("template_id", "professional_luxury")

        if background_url:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(background_url)
                bg_bytes = resp.content if resp.status_code == 200 else None

            if bg_bytes:
                gold_words = [w.strip() for w in new_gold.split(",") if w.strip()] if new_gold else BaseTemplate.extract_gold_words(new_headline)
                red_words = [w.strip() for w in new_red.split(",") if w.strip()] if new_red else BaseTemplate.extract_red_words(new_headline)

                inputs = TemplateInputs(
                    headline=new_headline,
                    website_url="binayah.com",
                    gold_words=gold_words,
                    red_words=red_words,
                    background_image_bytes=bg_bytes,
                )

                poster_path, tpl_name = select_and_render(inputs=inputs, output_dir="storage/images")

                with open(poster_path, "rb") as f:
                    poster_bytes = f.read()

                # Upload new poster
                from app.services.newsgen.storage import get_storage
                storage = get_storage()
                import uuid
                fname = f"poster_edit_{uuid.uuid4().hex[:8]}.png"
                new_url = await storage.save(f"images/{fname}", poster_bytes)

                update_fields["final_image_path"] = new_url
                update_fields["image_url"] = new_url
                update_fields["template_id"] = tpl_name

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Re-render failed, saving text only: {e}")

    await db.posts.update_one({"_id": ObjectId(post_id)}, {"$set": update_fields})

    return {"ok": True, "updated": list(update_fields.keys())}
