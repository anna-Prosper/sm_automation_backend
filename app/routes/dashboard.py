"""
DASHBOARD API ROUTES - POST REVIEW & MANAGEMENT
Backend endpoints for reviewing, editing, and managing generated posts
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import datetime, timedelta
from bson import ObjectId
import logging
import io
from app.utils.media import resolve_media_url

from app.db.session import get_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class PostUpdate(BaseModel):
    """Model for updating post content"""
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None
    status: Optional[Literal["draft", "approved", "rejected", "scheduled", "posted"]] = None
    scheduled_time: Optional[datetime] = None
    notes: Optional[str] = None
    story_status: Optional[Literal["draft", "approved"]] = None
    carousel_status: Optional[Literal["draft", "approved"]] = None


class ManualImageGeneration(BaseModel):
    """Model for manual image generation with custom prompt"""
    custom_prompt: str
    style: Literal["modern", "luxury", "minimal", "editorial"] = "luxury"
    platform: Literal["instagram_square", "instagram_portrait", "twitter"] = "instagram_square"


class RegenerateCaption(BaseModel):
    """Model for regenerating caption"""
    tone: Optional[str] = None
    include_stats: bool = True
    include_cta: bool = True


class GenerateFromArticle(BaseModel):
    """Model for generating a post from a manual article"""
    title: str
    content: str
    source: str = "Manual"
    url: Optional[str] = None        # source article URL
    platform: str = "instagram"
    generate_image: bool = True
    custom_image_prompt: Optional[str] = None
    article_image_url: Optional[str] = None


@router.get("/posts")
async def get_all_posts(
    status: Optional[str] = None,
    post_type: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
):
    """Get all posts with optional filtering."""
    db = await get_database()

    query = {}
    if status:
        query["status"] = status
    if post_type:
        query["post_type"] = post_type

    cursor = db.posts.find(query).sort("created_at", -1).skip(skip).limit(limit)
    posts = await cursor.to_list(length=limit)

    for post in posts:
        post["_id"] = str(post["_id"])
        
        # Convert S3 URLs to presigned URLs (2 hour expiry)
        for k in ("image_url", "background_image_path", "final_image_path"):
            if post.get(k):
                post[k] = resolve_media_url(post[k], expires_in=7200)

    total = await db.posts.count_documents(query)

    return {"posts": posts, "total": total, "limit": limit, "skip": skip}


@router.get("/posts/{post_id}")
async def get_post(post_id: str):
    """Get a single post by ID"""
    db = await get_database()
    try:
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    post["_id"] = str(post["_id"])
    
    for k in ("image_url", "background_image_path", "final_image_path"):
        if post.get(k):
            post[k] = resolve_media_url(post[k], expires_in=7200)

    return post


@router.get("/stats")
async def get_dashboard_stats():
    """Get dashboard statistics"""
    db = await get_database()

    total = await db.posts.count_documents({})
    draft = await db.posts.count_documents({"status": "draft"})
    approved = await db.posts.count_documents({"status": "approved"})
    rejected = await db.posts.count_documents({"status": "rejected"})
    scheduled = await db.posts.count_documents({"status": "scheduled"})
    posted = await db.posts.count_documents({"status": "posted"})
    complete = await db.posts.count_documents({"post_type": "complete"})

    week_ago = datetime.utcnow() - timedelta(days=7)
    recent = await db.posts.count_documents({"created_at": {"$gte": week_ago}})

    return {
        "total": total,
        "draft": draft,
        "approved": approved,
        "rejected": rejected,
        "scheduled": scheduled,
        "posted": posted,
        "complete_posts": complete,
        "recent_week": recent,
    }


@router.patch("/posts/{post_id}")
async def update_post(post_id: str, update: PostUpdate):
    """Update post content, status, or scheduling"""
    db = await get_database()

    update_doc = {"updated_at": datetime.utcnow()}

    if update.caption is not None:
        update_doc["caption"] = update.caption
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
        if post:
            hashtags = post.get("hashtags", [])
            update_doc["full_text"] = f"{update.caption}\n\n•\n•\n{' '.join('#' + t for t in hashtags)}"

    if update.hashtags is not None:
        update_doc["hashtags"] = update.hashtags
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
        if post:
            caption = post.get("caption", "")
            update_doc["full_text"] = f"{caption}\n\n•\n•\n{' '.join('#' + t for t in update.hashtags)}"

    if update.status is not None:
        update_doc["status"] = update.status
        if update.status == "approved":
            update_doc["approved_at"] = datetime.utcnow()
        elif update.status == "rejected":
            update_doc["rejected_at"] = datetime.utcnow()

    if update.scheduled_time is not None:
        update_doc["scheduled_time"] = update.scheduled_time
        update_doc["status"] = "scheduled"

    if update.notes is not None:
        update_doc["notes"] = update.notes

    if update.story_status is not None:
        update_doc["story_status"] = update.story_status
        if update.story_status == "approved":
            update_doc["story_approved_at"] = datetime.utcnow()
            # Also mark the main post as approved
            update_doc["status"] = "approved"
            update_doc["approved_at"] = datetime.utcnow()

    if update.carousel_status is not None:
        update_doc["carousel_status"] = update.carousel_status
        if update.carousel_status == "approved":
            update_doc["carousel_approved_at"] = datetime.utcnow()
            # Also mark the main post as approved
            update_doc["status"] = "approved"
            update_doc["approved_at"] = datetime.utcnow()

    try:
        result = await db.posts.update_one({"_id": ObjectId(post_id)}, {"$set": update_doc})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")

    updated_post = await db.posts.find_one({"_id": ObjectId(post_id)})
    updated_post["_id"] = str(updated_post["_id"])
    
    # Convert S3 URLs to presigned URLs
    for k in ("image_url", "background_image_path", "final_image_path"):
        if updated_post.get(k):
            updated_post[k] = resolve_media_url(updated_post[k], expires_in=7200)
    
    return updated_post


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str):
    """Delete a post"""
    db = await get_database()
    try:
        result = await db.posts.delete_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"success": True, "message": "Post deleted"}


@router.post("/posts/{post_id}/regenerate-image")
async def regenerate_image(post_id: str, request: ManualImageGeneration):
    """Regenerate image with custom prompt — uses template system for final poster."""
    db = await get_database()

    try:
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    from app.services.newsgen.image_generator import RealEstateImageGenerator, SimpleBackgroundGenerator
    from app.services.newsgen.pipeline import _upload_image
    from app.services.templates import select_and_render, TemplateInputs
    from app.services.templates.base_template import BaseTemplate

    generator = RealEstateImageGenerator()
    article = {"title": request.custom_prompt, "content": request.custom_prompt, "source": "Manual Generation"}

    try:
        # Step 1: Generate background
        bg_bytes = None
        image_data = generator.generate_post_image(
            article,
            style=request.style,
            platform=request.platform,
            add_branding=False,
        )
        if image_data and image_data.get("image_bytes"):
            bg_bytes = image_data["image_bytes"]

        if not bg_bytes:
            bg_gen = SimpleBackgroundGenerator()
            img = bg_gen.generate_gradient_background(1080, 1350, "elegant")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            bg_bytes = buf.getvalue()

        # Step 2: Template compositing
        headline = post.get("headline") or post.get("title", "Dubai Real Estate")
        gold_words = BaseTemplate.extract_gold_words(headline)
        red_words = BaseTemplate.extract_red_words(headline)
        sentiment = "negative" if red_words else "neutral"

        inputs = TemplateInputs(
            headline=headline,
            website_url="binayah.com",
            gold_words=gold_words,
            red_words=red_words,
            background_image_bytes=bg_bytes,
        )

        final_path, template_id = select_and_render(inputs=inputs, sentiment=sentiment)

        with open(final_path, "rb") as f:
            final_bytes = f.read()

        # Step 3: Upload
        fname = f"post_{template_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        image_url = await _upload_image(final_bytes, fname)

        bg_fname = f"bg_{post_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        bg_url = await _upload_image(bg_bytes, bg_fname)

        await db.posts.update_one(
            {"_id": ObjectId(post_id)},
            {
                "$set": {
                    "image_url": image_url,
                    "background_image_path": bg_url,
                    "final_image_path": image_url,
                    "template_id": template_id,
                    "custom_prompt": request.custom_prompt,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        # Return presigned URL to frontend
        return {
            "success": True, 
            "image_url": resolve_media_url(image_url, expires_in=7200), 
            "template_id": template_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("regenerate-image failed")
        raise HTTPException(status_code=500, detail=f"Image generation failed: {str(e)}")


@router.post("/posts/{post_id}/regenerate-caption")
async def regenerate_caption(post_id: str, request: RegenerateCaption):
    """Regenerate caption with different parameters"""
    db = await get_database()

    try:
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    from app.services.newsgen.post_creator import RealEstatePostCreator

    creator = RealEstatePostCreator()

    article = {
        "title": post.get("title", ""),
        "content": post.get("content", ""),
        "source": post.get("source_name", "News"),
    }

    try:
        platform = post.get("platform", "instagram")
        new_post = creator.create_post(
            article,
            platform=platform,
            include_stats=request.include_stats,
            include_cta=request.include_cta,
        )

        await db.posts.update_one(
            {"_id": ObjectId(post_id)},
            {"$set": {"caption": new_post["caption"], "full_text": new_post["full_text"], "updated_at": datetime.utcnow()}},
        )

        return {"success": True, "caption": new_post["caption"], "full_text": new_post["full_text"]}

    except Exception as e:
        logger.exception("regenerate-caption failed")
        raise HTTPException(status_code=500, detail=f"Caption regeneration failed: {str(e)}")


@router.post("/posts/{post_id}/regenerate-hashtags")
async def regenerate_hashtags(post_id: str, platform: str = "instagram"):
    """Regenerate hashtags for a post"""
    db = await get_database()

    try:
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    from app.services.newsgen.post_creator import RealEstatePostCreator

    creator = RealEstatePostCreator()

    article = {
        "title": post.get("title", ""),
        "content": post.get("content", ""),
        "source": post.get("source_name", "News"),
    }

    try:
        hashtags = creator._generate_hashtags(article, platform)

        caption = post.get("caption", "")
        full_text = f"{caption}\n\n•\n•\n{' '.join('#' + t for t in hashtags)}"

        await db.posts.update_one(
            {"_id": ObjectId(post_id)},
            {"$set": {"hashtags": hashtags, "full_text": full_text, "updated_at": datetime.utcnow()}},
        )

        return {"success": True, "hashtags": hashtags}

    except Exception as e:
        logger.exception("regenerate-hashtags failed")
        raise HTTPException(status_code=500, detail=f"Hashtag regeneration failed: {str(e)}")


@router.post("/posts/bulk-approve")
async def bulk_approve(post_ids: List[str]):
    """Approve multiple posts at once"""
    db = await get_database()
    object_ids = [ObjectId(pid) for pid in post_ids]
    result = await db.posts.update_many(
        {"_id": {"$in": object_ids}},
        {"$set": {"status": "approved", "approved_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )
    return {"success": True, "updated": result.modified_count}


@router.post("/posts/bulk-reject")
async def bulk_reject(post_ids: List[str]):
    """Reject multiple posts at once"""
    db = await get_database()
    object_ids = [ObjectId(pid) for pid in post_ids]
    result = await db.posts.update_many(
        {"_id": {"$in": object_ids}},
        {"$set": {"status": "rejected", "rejected_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )
    return {"success": True, "updated": result.modified_count}


@router.post("/posts/bulk-delete")
async def bulk_delete(post_ids: List[str]):
    """Delete multiple posts at once"""
    db = await get_database()
    object_ids = [ObjectId(pid) for pid in post_ids]
    result = await db.posts.delete_many({"_id": {"$in": object_ids}})
    return {"success": True, "deleted": result.deleted_count}


@router.post("/generate-from-article")
async def generate_post_from_article(payload: GenerateFromArticle):
    """
    Generate a complete post from manually-provided article text.
    Uses the template compositing system for final image.
    """
    from app.services.newsgen.post_creator import RealEstatePostCreator
    from app.services.newsgen.image_generator import RealEstateImageGenerator, SimpleBackgroundGenerator
    from app.services.newsgen.pipeline import _upload_image
    from app.services.templates import select_and_render, TemplateInputs
    from app.services.templates.base_template import BaseTemplate

    db = await get_database()

    article = {
        "title": payload.title,
        "content": payload.content,
        "source": payload.source,
        "url": payload.url or "",
    }

    try:
        # Caption + hashtags
        creator = RealEstatePostCreator()
        post_data = creator.create_post(article, platform=payload.platform)

        # Defaults
        image_url = None
        bg_url = None
        template_id = "none"
        headline = (payload.title or "")[:120].strip() or "Dubai Real Estate Update"

        gold_words = list(BaseTemplate.extract_gold_words(headline))
        red_words = list(BaseTemplate.extract_red_words(headline))

        if payload.generate_image:
            img_article = dict(article)

            if payload.custom_image_prompt:
                img_article["title"] = payload.custom_image_prompt
                img_article["content"] = payload.custom_image_prompt

            # Step 1: Generate background
            # Priority 1: Transform article image → Priority 2: Generate from scratch → Priority 3: Gradient
            bg_bytes = None
            ai_prompt = None

            try:
                gen = RealEstateImageGenerator()

                if payload.article_image_url:
                    logger.info(f"Transforming article image: {payload.article_image_url}")
                    image_data = gen.transform_article_image(
                        article_image_url=payload.article_image_url,
                        article=img_article,
                        style="luxury",
                        transformation_strength=0.7,
                        platform="instagram_portrait",
                    )
                    if image_data and image_data.get("image_bytes"):
                        bg_bytes = image_data["image_bytes"]
                        ai_prompt = image_data.get("prompt") or None
                        logger.info("Article image transformed successfully")

                if not bg_bytes:
                    logger.info("Generating background from scratch with Stability AI")
                    image_data = gen.generate_post_image(
                        img_article,
                        style="luxury",
                        platform="instagram_portrait",
                        add_branding=False,
                    )
                    if image_data and image_data.get("image_bytes"):
                        bg_bytes = image_data["image_bytes"]
                        ai_prompt = image_data.get("prompt") or None

            except Exception as e:
                logger.warning(f"Background generation failed, using gradient: {e}")
                bg_bytes = None

            if not bg_bytes:
                bg_gen = SimpleBackgroundGenerator()
                img = bg_gen.generate_gradient_background(1080, 1350, "elegant")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                bg_bytes = buf.getvalue()

            # Step 2: Template compositing
            try:
                inputs = TemplateInputs(
                    headline=headline,
                    website_url="binayah.com",
                    gold_words=set(gold_words),
                    red_words=set(red_words),
                    background_image_bytes=bg_bytes,
                )
                sentiment = "negative" if red_words else "neutral"
                final_path, template_id = select_and_render(inputs=inputs, sentiment=sentiment)

                with open(final_path, "rb") as f:
                    final_bytes = f.read()

                fname = f"post_{template_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                image_url = await _upload_image(final_bytes, fname)

                bg_fname = f"bg_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                bg_url = await _upload_image(bg_bytes, bg_fname)

            except Exception as e:
                logger.exception("Template render failed, using background as final image")
                fname = f"manual_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                image_url = await _upload_image(bg_bytes, fname)
                bg_url = image_url

        # Save to database
        post_doc = {
            "title": payload.title,
            "content": payload.content[:2000],
            "source_url": payload.url or "",
            "source_name": payload.source,
            "caption": post_data.get("caption", ""),
            "full_text": post_data.get("full_text", ""),
            "hashtags": post_data.get("hashtags", []),
            "platform": payload.platform,
            "image_url": image_url,
            "background_image_path": bg_url or image_url,
            "final_image_path": image_url,
            "headline": headline,
            "template_id": template_id,
            "gold_words": gold_words,
            "red_words": red_words,
            "ai_prompt": ai_prompt if payload.generate_image else None,
            "status": "draft",
            "post_type": "complete" if image_url else "basic",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        result = await db.posts.insert_one(post_doc)
        post_doc["_id"] = str(result.inserted_id)
        
        # Convert S3 URLs to presigned URLs for response
        for k in ("image_url", "background_image_path", "final_image_path"):
            if post_doc.get(k):
                post_doc[k] = resolve_media_url(post_doc[k], expires_in=7200)

        return {"success": True, "post": post_doc}

    except Exception as e:
        logger.exception("generate-from-article failed")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")




@router.get("/home")
async def get_dashboard_home():
    """
    Single endpoint for the Content Studio dashboard.
    Returns stats, auto-post info, ALL ready posts, posted history.
    Also checks for overdue auto-posts (in case scheduler missed them).
    """
    db = await get_database()
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # ── CHECK FOR OVERDUE AUTO-POSTS ──────────────────────
    # This is the safety net: if APScheduler job was lost (worker restart),
    # catch it here when the user opens the dashboard.
    try:
        overdue = await db.pipeline_cycles.find_one({
            "status": "pending_review",
            "auto_post_at": {"$lte": now},
        })
        if overdue:
            logger.info("DASHBOARD: Found overdue auto-post for cycle %s, triggering now", overdue.get("cycle_id"))
            from app.scheduler import delayed_auto_post
            import asyncio
            asyncio.create_task(delayed_auto_post(cycle_id=overdue.get("cycle_id", "dashboard_recovery")))
    except Exception as e:
        logger.warning("Overdue auto-post check failed: %s", e)

    # ── Stats ─────────────────────────────────────────────
    total = await db.posts.count_documents({})
    draft = await db.posts.count_documents({"status": "draft"})
    approved = await db.posts.count_documents({"status": "approved"})
    posted_count = await db.posts.count_documents({"status": "posted"})
    recent_week = await db.posts.count_documents({"created_at": {"$gte": week_ago}})

    stats = {
        "total": total, "draft": draft, "approved": approved,
        "posted": posted_count, "recent_week": recent_week,
    }

    # ── Auto-post timer (from latest pending cycle) ───────
    cycle = await db.pipeline_cycles.find_one(
        {"status": "pending_review"},
        sort=[("started_at", -1)],
    )
    seconds_left = 0
    cycle_info = None
    if cycle:
        auto_post_at = cycle.get("auto_post_at")
        if auto_post_at:
            if isinstance(auto_post_at, str):
                auto_post_at = datetime.fromisoformat(auto_post_at)
            seconds_left = max(0, int((auto_post_at - now).total_seconds()))
        cycle_info = {
            "cycle_id": cycle.get("cycle_id"),
            "auto_post_at": auto_post_at.isoformat() if isinstance(auto_post_at, datetime) else auto_post_at,
            "status": cycle.get("status"),
            "fetched": cycle.get("articles_fetched", 0),
            "approved": cycle.get("articles_approved", 0),
            "generated": cycle.get("posts_generated", 0),
        }

    # ── Ready posts: current cycle + previous cycle only ────
    # Get last 2 cycles
    recent_cycles = []
    async for cyc in db.pipeline_cycles.find().sort("started_at", -1).limit(2):
        recent_cycles.append(cyc)

    ready_posts = []
    current_cycle_ids = set()
    prev_cycle_ids = set()

    for idx, cyc in enumerate(recent_cycles):
        cycle_post_ids = cyc.get("post_ids", [])
        cycle_label = "current" if idx == 0 else "previous"
        cycle_time = cyc.get("started_at")

        for pid in cycle_post_ids:
            try:
                post = await db.posts.find_one({"_id": ObjectId(pid)})
                if post and post.get("status") in ("draft", "approved"):
                    post["_id"] = str(post["_id"])
                    post["cycle_label"] = cycle_label
                    post["cycle_time"] = cycle_time.isoformat() if isinstance(cycle_time, datetime) else cycle_time
                    for k in ("image_url", "background_image_path", "final_image_path"):
                        if post.get(k):
                            post[k] = resolve_media_url(post[k], expires_in=7200)
                    ready_posts.append(post)
                    if idx == 0:
                        current_cycle_ids.add(str(post["_id"]))
                    else:
                        prev_cycle_ids.add(str(post["_id"]))
            except Exception:
                pass

    # Sort: current cycle first (by score), then previous cycle (by score)
    ready_posts.sort(key=lambda p: (0 if p.get("cycle_label") == "current" else 1, -(p.get("relevance_score") or 0)))

    # Determine auto-pick: approved post first, else highest-scored from current cycle
    auto_pick_id = None
    approved_ready = [p for p in ready_posts if p.get("status") == "approved"]
    current_ready = [p for p in ready_posts if p.get("cycle_label") == "current"]
    if approved_ready:
        auto_pick_id = approved_ready[0]["_id"]
    elif current_ready:
        auto_pick_id = current_ready[0]["_id"]
    elif ready_posts:
        auto_pick_id = ready_posts[0]["_id"]

    # ── Recently posted ───────────────────────────────────
    posted_posts = []
    async for post in db.posts.find(
        {"status": "posted"}
    ).sort("published_at", -1).limit(6):
        post["_id"] = str(post["_id"])
        for k in ("image_url", "background_image_path", "final_image_path"):
            if post.get(k):
                post[k] = resolve_media_url(post[k], expires_in=7200)
        posted_posts.append(post)

    # ── Activity (last 7 days) ────────────────────────────
    events = []
    async for cyc in db.pipeline_cycles.find(
        {"started_at": {"$gte": week_ago}}
    ).sort("started_at", -1).limit(10):
        started = cyc.get("started_at")
        events.append({
            "type": "cycle",
            "time": started.isoformat() if isinstance(started, datetime) else started,
            "fetched": cyc.get("articles_fetched", 0),
            "approved": cyc.get("articles_approved", 0),
            "generated": cyc.get("posts_generated", 0),
        })

    async for post in db.posts.find(
        {"published_at": {"$gte": week_ago}},
        {"title": 1, "published_at": 1, "source_name": 1, "auto_posted_at": 1}
    ).sort("published_at", -1).limit(10):
        pub = post.get("published_at")
        events.append({
            "type": "posted",
            "time": pub.isoformat() if isinstance(pub, datetime) else pub,
            "title": (post.get("title") or "")[:60],
            "auto": bool(post.get("auto_posted_at")),
        })

    events.sort(key=lambda e: e.get("time", ""), reverse=True)

    # ── Pipeline summary (latest cycle scoring stats) ─────
    pipeline_summary = None
    latest_cycle = recent_cycles[0] if recent_cycles else None
    if latest_cycle:
        # Get top topics from scored articles in last 24h
        day_ago = now - timedelta(hours=24)
        topic_pipeline = [
            {"$match": {"scored_at": {"$gte": day_ago}, "status": "approved", "topic": {"$exists": True, "$ne": "General"}}},
            {"$group": {"_id": "$topic", "count": {"$sum": 1}, "avg_score": {"$avg": "$relevance_score"}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]
        top_topics = []
        try:
            async for doc in db.scored_articles.aggregate(topic_pipeline):
                top_topics.append({"topic": doc["_id"], "count": doc["count"], "avg_score": round(doc.get("avg_score", 0))})
        except Exception:
            pass

        total_scored_24h = await db.scored_articles.count_documents({"scored_at": {"$gte": day_ago}})
        approved_scored_24h = await db.scored_articles.count_documents({"scored_at": {"$gte": day_ago}, "status": "approved"})
        approval_rate = round(approved_scored_24h / total_scored_24h * 100) if total_scored_24h > 0 else 0

        pipeline_summary = {
            "articles_scored": total_scored_24h,
            "articles_approved": approved_scored_24h,
            "approval_rate": approval_rate,
            "top_topics": top_topics,
            "last_cycle": {
                "fetched": latest_cycle.get("articles_fetched", 0),
                "approved": latest_cycle.get("articles_approved", 0),
                "generated": latest_cycle.get("posts_generated", 0),
                "time": latest_cycle.get("started_at").isoformat() if isinstance(latest_cycle.get("started_at"), datetime) else latest_cycle.get("started_at"),
            }
        }

    return {
        "stats": stats,
        "auto_post": {
            "has_timer": seconds_left > 0,
            "seconds_left": seconds_left,
            "auto_pick_id": auto_pick_id,
            "cycle": cycle_info,
        },
        "ready_posts": ready_posts,
        "posted_posts": posted_posts,
        "activity": events[:15],
        "pipeline_summary": pipeline_summary,
    }

@router.get("/up-next")
async def get_up_next():
    """
    Get the current pending review cycle: posts waiting for approval,
    countdown timer, and which post will auto-post.
    """
    db = await get_database()
    now = datetime.utcnow()
    
    # Find most recent pending cycle
    cycle = await db.pipeline_cycles.find_one(
        {"status": "pending_review"},
        sort=[("started_at", -1)],
    )
    
    if not cycle:
        # Check if there's a recently completed cycle (last 2 hours)
        two_hours_ago = now - timedelta(hours=2)
        cycle = await db.pipeline_cycles.find_one(
            {"started_at": {"$gte": two_hours_ago}},
            sort=[("started_at", -1)],
        )
    
    if not cycle:
        return {"has_pending": False, "posts": [], "cycle": None}
    
    # Get the posts for this cycle
    post_ids = cycle.get("post_ids", [])
    posts = []
    for pid in post_ids:
        try:
            post = await db.posts.find_one({"_id": ObjectId(pid)})
            if post:
                post["_id"] = str(post["_id"])
                for k in ("image_url", "background_image_path", "final_image_path"):
                    if post.get(k):
                        post[k] = resolve_media_url(post[k], expires_in=7200)
                posts.append(post)
        except Exception:
            pass
    
    # Sort by ai_score descending
    posts.sort(key=lambda p: p.get("ai_score") or p.get("relevance_score") or 0, reverse=True)
    
    # Calculate countdown
    auto_post_at = cycle.get("auto_post_at")
    seconds_left = 0
    if auto_post_at:
        if isinstance(auto_post_at, str):
            auto_post_at = datetime.fromisoformat(auto_post_at)
        seconds_left = max(0, int((auto_post_at - now).total_seconds()))
    
    # Check if expired (past auto_post_at)
    expired = seconds_left <= 0 and cycle.get("status") == "pending_review"
    
    # Find which post will auto-post (approved one, or highest scored)
    auto_pick_id = None
    approved_posts = [p for p in posts if p.get("status") == "approved"]
    if approved_posts:
        auto_pick_id = approved_posts[0]["_id"]
    elif posts:
        auto_pick_id = posts[0]["_id"]
    
    return {
        "has_pending": len(posts) > 0 and not expired,
        "cycle_id": cycle.get("cycle_id"),
        "started_at": cycle.get("started_at").isoformat() if cycle.get("started_at") else None,
        "auto_post_at": auto_post_at.isoformat() if auto_post_at else None,
        "seconds_left": seconds_left,
        "status": cycle.get("status"),
        "auto_pick_id": auto_pick_id,
        "posts": posts,
        "stats": {
            "fetched": cycle.get("articles_fetched", 0),
            "scored": cycle.get("articles_scored", 0),
            "approved": cycle.get("articles_approved", 0),
            "generated": cycle.get("posts_generated", 0),
        },
    }


@router.get("/activity")
async def get_activity_feed(limit: int = 20):
    """
    Timeline of pipeline activity for the past 7 days.
    """
    db = await get_database()
    week_ago = datetime.utcnow() - timedelta(days=7)
    
    # Get recent cycles
    cycles = []
    cursor = db.pipeline_cycles.find(
        {"started_at": {"$gte": week_ago}}
    ).sort("started_at", -1).limit(limit)
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        cycles.append(doc)
    
    # Get recent posts with their publish status
    recent_posts = []
    post_cursor = db.posts.find(
        {"created_at": {"$gte": week_ago}},
        {"title": 1, "status": 1, "created_at": 1, "published_at": 1, "source_name": 1, "auto_posted_at": 1}
    ).sort("created_at", -1).limit(50)
    async for doc in post_cursor:
        doc["_id"] = str(doc["_id"])
        recent_posts.append(doc)
    
    # Build unified timeline
    events = []
    for cycle in cycles:
        started = cycle.get("started_at")
        if isinstance(started, datetime):
            started = started.isoformat()
        events.append({
            "type": "cycle_start",
            "time": started,
            "data": {
                "fetched": cycle.get("articles_fetched", 0),
                "scored": cycle.get("articles_scored", 0),
                "approved": cycle.get("articles_approved", 0),
                "generated": cycle.get("posts_generated", 0),
            }
        })
    
    for post in recent_posts:
        if post.get("published_at"):
            pub = post["published_at"]
            if isinstance(pub, datetime):
                pub = pub.isoformat()
            events.append({
                "type": "post_published",
                "time": pub,
                "data": {
                    "title": post.get("title", "")[:60],
                    "source": post.get("source_name", ""),
                    "auto": bool(post.get("auto_posted_at")),
                }
            })
    
    # Sort by time descending
    events.sort(key=lambda e: e.get("time", ""), reverse=True)
    
    return {"events": events[:limit]}


@router.post("/up-next/{post_id}/approve-for-posting")
async def approve_for_auto_post(post_id: str):
    """
    Approve a specific post from the Up Next cycle.
    This post will be the one that auto-posts when the timer expires.
    """
    db = await get_database()
    
    try:
        oid = ObjectId(post_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")
    
    post = await db.posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Set this post to approved
    await db.posts.update_one(
        {"_id": oid},
        {"$set": {"status": "approved", "updated_at": datetime.utcnow()}}
    )
    
    # Un-approve siblings in the same cycle
    cycle = await db.pipeline_cycles.find_one(
        {"post_ids": post_id, "status": "pending_review"},
        sort=[("started_at", -1)],
    )
    if cycle:
        sibling_ids = [pid for pid in cycle.get("post_ids", []) if pid != post_id]
        for sid in sibling_ids:
            try:
                await db.posts.update_one(
                    {"_id": ObjectId(sid), "status": "approved"},
                    {"$set": {"status": "draft", "updated_at": datetime.utcnow()}}
                )
            except Exception:
                pass
    
    return {"success": True, "approved_post_id": post_id}
