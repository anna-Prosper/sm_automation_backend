from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from bson import ObjectId

from app.services.templates.template_selector import get_template_selector, TemplateInputs
from app.services.templates.base_template import BaseTemplate
from app.db.session import get_database
from app.services.templates.rendering_helpers import generate_filename
from app.utils.media import resolve_media_url

router = APIRouter()


@router.get("")
@router.get("/")
async def get_templates():
    """List all available post templates."""
    selector = get_template_selector()
    templates = []
    for name in selector.get_all_template_names():
        t = selector.get_template_by_name(name)
        templates.append({
            "id": name,
            "name": name.replace("_", " ").title(),
            "description": _template_descriptions.get(name, ""),
        })
    return templates


@router.get("/{template_id}")
async def get_template(template_id: str):
    """Get details for a specific template."""
    selector = get_template_selector()
    t = selector.get_template_by_name(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return {
        "id": template_id,
        "name": template_id.replace("_", " ").title(),
        "description": _template_descriptions.get(template_id, ""),
    }


class ReRenderRequest(BaseModel):
    template_id: str
    headline: Optional[str] = None


@router.post("/{post_id}/rerender")
async def rerender_post_with_template(post_id: str, req: ReRenderRequest):
    """
    Re-render an existing post with a different template.
    Reuses the stored background image — only re-composites.
    """
    db = await get_database()

    try:
        post = await db.posts.find_one({"_id": ObjectId(post_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid post ID")

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Load the stored background image bytes
    bg_bytes = await _load_background_bytes(post)
    if not bg_bytes:
        bg_path = post.get("background_image_path") or post.get("image_url") or "None"
        raise HTTPException(
            status_code=400, 
            detail=f"No background image available for this post. Path: {bg_path[:100]}"
        )

    headline = req.headline or post.get("headline") or post.get("title", "Dubai Real Estate")
    gold_words = BaseTemplate.extract_gold_words(headline)
    red_words = BaseTemplate.extract_red_words(headline)

    selector = get_template_selector()

    inputs = TemplateInputs(
        headline=headline,
        website_url="binayah.com",
        gold_words=gold_words,
        background_image_bytes=bg_bytes,
        red_words=red_words,
        location_tag=post.get("location_tag"),
    )

    try:
        final_bytes = selector.render_with_template_bytes(req.template_id, inputs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template rendering failed: {e}")

    # Upload new final image
    from app.services.newsgen.storage import get_storage
    storage = get_storage()
    fname = generate_filename(prefix=f"rerender_{req.template_id}", extension="png")
    new_url = await storage.save(f"images/{fname}", final_bytes)

    # Update post in DB
    await db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": {
            "image_url": new_url,
            "final_image_path": new_url,
            "template_id": req.template_id,
            "headline": headline,
            "gold_words": list(gold_words),
            "red_words": list(red_words),
        }}
    )

    return {
        "success": True,
        "image_url": resolve_media_url(new_url, expires_in=7200),
        "template_id": req.template_id,
        "headline": headline,
    }


async def _load_background_bytes(post: dict) -> Optional[bytes]:
    """Load background image from stored path/URL."""
    import requests as http_requests
    from app.utils.media import resolve_media_url

    bg_path = post.get("background_image_path")
    if not bg_path:
        bg_path = post.get("image_url")
    if not bg_path:
        return None

    try:
        if bg_path.startswith("http"):
            if "s3" in bg_path and "amazonaws.com" in bg_path:
                # Generate presigned URL for private S3 bucket
                presigned_url = resolve_media_url(bg_path, expires_in=3600)
                resp = http_requests.get(presigned_url, timeout=30)
            else:
                # Regular HTTP URL
                resp = http_requests.get(bg_path, timeout=30)
            
            resp.raise_for_status()
            return resp.content
        else:
            # Local path
            import os
            for candidate in [bg_path, f"apps/api/app{bg_path}", bg_path.lstrip("/")]:
                if os.path.exists(candidate):
                    with open(candidate, "rb") as f:
                        return f.read()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to load background image from {bg_path}: {e}")
    return None


_template_descriptions = {
    "professional_luxury": "Clean, cinematic style with vignette + bottom fade. "
                           "Gold keyword highlights. Best for launches, partnerships, premium news.",
    "bold_market": "Dark, high-contrast design with heavy vignette. "
                   "Bold headlines with red/gold highlights. Best for market news, records, urgent updates.",
    "elegant_minimal": "Modern minimal with solid teal bottom bar. "
                       "Clean and Instagram-friendly. Best for lifestyle, community, general updates.",
}