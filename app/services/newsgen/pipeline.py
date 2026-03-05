"""
COMPLETE NEWS PIPELINE
Fetch → Confidence Filter → Deduplicate → Validate → LLM Rank → Post Creation (Top 3) → Save
"""

import io
import base64
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

from app.services.newsgen.multi_api_fetcher import Config as FetcherConfig
from app.services.newsgen.multi_api_fetcher import fetch_all_news, search_news
from app.services.templates import select_and_render, TemplateInputs
from app.services.templates.base_template import BaseTemplate
from app.db.session import get_database
from app.services.newsgen.llm_scorer import LLMArticleScorer

logger = logging.getLogger(__name__)

FETCH_STATE_COLLECTION = "news_fetch_state"
FETCH_STATE_DOC_ID = "pipeline_fetch_window"


async def _upload_image(image_bytes: bytes, filename: str) -> Optional[str]:
    """Upload image via configured storage backend (S3/dual/local)."""
    try:
        from app.services.newsgen.storage import get_storage
        storage = get_storage()
        path = f"images/{filename}"
        url = await storage.save(path, image_bytes)
        logger.info(f"    ✅ Image uploaded → {url}")
        return url
    except Exception as e:
        logger.warning(f"    ⚠️ Image upload failed: {e}")
        return None
    

async def _deduplicate_articles(articles: List[Dict]) -> List[Dict]:
    """
    Remove articles already scored or posted.
    Checks scored_articles + posts by URL to avoid re-processing.
    """
    db = await get_database()
    
    # Build set of all known URLs (one batch query each)
    urls = [a.get("url", "") for a in articles if a.get("url")]
    if not urls:
        return articles
    
    scored_cursor = db.scored_articles.find(
        {"source_url": {"$in": urls}}, {"source_url": 1}
    )
    scored_urls = set()
    async for doc in scored_cursor:
        scored_urls.add(doc.get("source_url", ""))
    
    posted_cursor = db.posts.find(
        {"source_url": {"$in": urls}}, {"source_url": 1}
    )
    async for doc in posted_cursor:
        scored_urls.add(doc.get("source_url", ""))
    
    new_articles = [a for a in articles if a.get("url", "") not in scored_urls]
    logger.info(f"  Deduplication: {len(articles)} -> {len(new_articles)} new (skipped {len(scored_urls)} known)")
    return new_articles


async def _generate_complete_posts(
    top_articles: List[Dict],
    num_posts: int = 3,
    platform: str = "instagram",
) -> List[Dict]:
    """
    Generate complete posts with image + caption for top N articles.
    Uses RealEstateImageGenerator for background and template system for rendering.
    """
    try:
        from app.services.newsgen.post_creator import RealEstatePostCreator, MultiPlatformPostCreator
        from app.services.newsgen.image_generator import RealEstateImageGenerator, SimpleBackgroundGenerator
        from app.services.templates import select_and_render, TemplateInputs
        from app.services.templates.base_template import BaseTemplate
    except Exception as exc:
        logger.error(f"❌ Could not import post-creation modules: {exc}")
        return []

    creator = RealEstatePostCreator()
    multi_creator = MultiPlatformPostCreator()
    image_generator = RealEstateImageGenerator()
    complete_posts: List[Dict] = []

    for i, article in enumerate(top_articles[:num_posts], 1):
        logger.info(f"\n  [{i}/{num_posts}] 🎨 Creating post: {article['title'][:60]}…")

        # STEP 1: Generate Caption & Hashtags
        try:
            post_data = creator.create_post(article, platform=platform)
            caption = post_data.get("caption", "")
            hashtags = post_data.get("hashtags", [])
            headline = post_data.get("headline", article['title'][:80])
        except Exception as e:
            logger.warning(f"    ⚠️ Caption generation failed: {e}")
            post_data = creator._create_fallback_post(article, platform)
            caption = post_data.get("caption", "")
            hashtags = post_data.get("hashtags", [])
            headline = article['title'][:80]

        # STEP 1b: Generate per-platform content (all 5 platforms in one call)
        platforms_content = {}
        try:
            platforms_content = multi_creator.create_all_platforms(article)
            logger.info(f"    ✅ Platform content generated for {len(platforms_content)} platforms")
        except Exception as e:
            logger.warning(f"    ⚠️ Multi-platform generation failed: {e}")

        # STEP 2: Extract Keywords for Highlighting
        gold_words = BaseTemplate.extract_gold_words(headline)
        red_words = BaseTemplate.extract_red_words(headline)

        logger.info(f"    ✨ Gold: {', '.join(gold_words) if gold_words else 'none'}")
        logger.info(f"    🔴 Red:  {', '.join(red_words) if red_words else 'none'}")

        # STEP 3: Generate Background (Priority: Transform Article Image → Generate from Scratch → Gradient)
        background_bytes = None
        background_provider = None
        ai_prompt = None
        article_image_url = article.get("article_image_url")

        # Sanitize image URL if present
        if article_image_url:
            article_image_url = article_image_url.strip()
            if not article_image_url.startswith("http"):
                article_image_url = None

        # Priority 1: Transform article image if available
        if article_image_url:
            try:
                logger.info(f"    🎨 Trying article image transformation...")
                image_data = image_generator.transform_article_image(
                    article_image_url=article_image_url,
                    article=article,
                    style="luxury",
                    transformation_strength=0.5,  # 70% transformation - good balance
                    platform="instagram_portrait",
                )
                if image_data and image_data.get("image_bytes"):
                    background_bytes = image_data["image_bytes"]
                    background_provider = "article_transformed"
                    ai_prompt = image_data.get("prompt") or ""
                    logger.info(f"    ✅ Background: Article image transformed")
            except Exception as e:
                logger.warning(f"    ⚠️ Article image transformation skipped: {e}")
                article_image_url = None  # Mark as failed, try next method

        # Priority 2: Generate from scratch if no article image
        if not background_bytes:
            try:
                logger.info(f"    🎨 Generating background from scratch with Stability AI...")
                image_data = image_generator.generate_post_image(
                    article,
                    style="luxury",
                    platform="instagram_portrait",
                    add_branding=False,
                )
                if image_data and image_data.get("image_bytes"):
                    background_bytes = image_data["image_bytes"]
                    background_provider = "stability_ai"
                    ai_prompt = image_data.get("prompt") or ""
                    logger.info(f"    ✅ Background: Generated from scratch")
            except Exception as e:
                logger.warning(f"    ⚠️ Background generation failed: {e}")

        # Priority 3: Gradient fallback
        if not background_bytes:
            try:
                logger.info(f"    🎨 Using gradient fallback...")
                bg_gen = SimpleBackgroundGenerator()
                img = bg_gen.generate_gradient_background(1080, 1350, "elegant")
                import io as _io
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                background_bytes = buf.getvalue()
                background_provider = "gradient"
                logger.info("    ✅ Gradient background created")
            except Exception as e:
                logger.warning(f"    ⚠️ Gradient fallback also failed: {e}")

        #STEP 4: Render with Template System
        poster_bytes = None
        template_name = None

        try:
            sentiment = "neutral"
            headline_lower = headline.lower()
            if any(w in headline_lower for w in ["crash", "drop", "fall", "decline", "warning"]):
                sentiment = "negative"
            elif any(w in headline_lower for w in ["surge", "growth", "rise", "launch", "new"]):
                sentiment = "positive"

            template_inputs = TemplateInputs(
                headline=headline,
                website_url="binayah.com",
                gold_words=gold_words,
                red_words=red_words,
                background_image_bytes=background_bytes,
            )

            poster_path, template_name = select_and_render(
                inputs=template_inputs,
                sentiment=sentiment,
                output_dir="storage/images",
            )

            with open(poster_path, "rb") as f:
                poster_bytes = f.read()

            logger.info(f"    ✅ Template: {template_name}")

        except Exception as e:
            logger.warning(f"    ⚠️ Template rendering failed: {e}")

        # STEP 5: Upload
        background_url = None
        poster_url = None

        if background_bytes:
            bg_filename = f"bg_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{i}.png"
            background_url = await _upload_image(background_bytes, bg_filename)

        if poster_bytes:
            poster_filename = f"poster_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{i}.png"
            poster_url = await _upload_image(poster_bytes, poster_filename)

        enriched = {
            **article,
            "headline": headline,
            "caption": caption,
            "hashtags": hashtags,
            "full_text": post_data.get("full_text", ""),
            "platform": platform,
            "background_image_path": background_url,
            "final_image_path": poster_url,
            "image_url": poster_url,
            "image_width": 1080,
            "image_height": 1350,
            "gold_words": ",".join(sorted(gold_words)) if gold_words else "",
            "red_words": ",".join(sorted(red_words)) if red_words else "",
            "background_provider": background_provider,
            "ai_prompt": ai_prompt,
            "template_id": template_name or "professional_luxury",
            "post_format": "feed",
            "platforms": platforms_content,
        }

        complete_posts.append(enriched)
        logger.info(f"    ✅ Post {i} complete")

    return complete_posts


async def _save_scored_articles(articles: List[Dict]) -> int:
    """Save LLM-scored articles to scored_articles collection."""
    db = await get_database()
    saved = 0
    now = datetime.utcnow()
    for a in articles:
        url = a.get("url", "")
        if not url:
            continue
        doc = {
            "title": a.get("title", ""),
            "content": (a.get("content") or a.get("body") or "")[:2000],
            "source_url": url,
            "url": url,
            "source": a.get("source", "Unknown"),
            "source_domain": a.get("source_domain", ""),
            "published_date": a.get("published_date", ""),
            "article_image_url": a.get("article_image_url"),
            "relevance_score": a.get("relevance_score", 0),
            "positivity": a.get("positivity", "neutral"),
            "llm_reason": a.get("llm_reason", ""),
            "topic": a.get("topic", "General"),
            "status": a.get("status", "approved"),
            "scored_at": now,
            "updated_at": now,
        }
        result = await db.scored_articles.update_one(
            {"url": url},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        if result.upserted_id is not None:
            saved += 1
    logger.info(f"  Saved {saved} scored articles (of {len(articles)})")
    return saved


async def _save_complete_posts(posts: List[Dict]) -> int:
    """Save complete posts (with images) to database"""
    db = await get_database()
    saved = 0
    now = datetime.utcnow()

    for post in posts:
        try:
            post_doc = {
                # Source info
                "title": post["title"],
                "content": post.get("content", "")[:2000],
                "source_url": post.get("url", ""),
                "source_name": post.get("source", "Unknown"),
                "published_date": post.get("published_date"),
                "api_tier": post.get("api_tier", "EventRegistry"),
                
                # Scores
                "confidence_score": post.get("confidence_score"),
                "relevance_score": post.get("relevance_score"),
                "ai_reasoning": post.get("ai_reasoning"),
                "topic": post.get("topic", "General"),
                
                # Post content
                "headline": post.get("headline"),
                "caption": post.get("caption"),
                "full_text": post.get("full_text"),
                "hashtags": post.get("hashtags", []),
                "platform": post.get("platform", "instagram"),
                
                # Images
                "background_image_path": post.get("background_image_path"),
                "final_image_path": post.get("final_image_path"),
                "image_url": post.get("image_url"),
                "image_width": post.get("image_width"),
                "image_height": post.get("image_height"),
                
                # Rendering config
                "gold_words": post.get("gold_words"),
                "red_words": post.get("red_words"),
                "background_provider": post.get("background_provider"),
                "background_ref": post.get("background_ref"),
                "ai_prompt": post.get("ai_prompt"),
                "template_id": post.get("template_id", "binayah_default"),
                
                # Status
                "status": "draft",
                "post_type": "complete",
                "post_format": post.get("post_format", "feed"),
                
                # Per-platform content (Step 2)
                "platforms": post.get("platforms") or {},
                
                # Metadata
                "created_at": now,
                "updated_at": now,
                "published_at": None,
            }
            
            result = await db.posts.update_one(
                {"source_url": post.get("url", "")},
                {"$set": post_doc},
                upsert=True,
            )
            
            if result.upserted_id or result.modified_count:
                saved += 1
                
        except Exception as e:
            logger.error(f"❌ Error saving complete post: {e}")

    return saved


async def _save_basic_posts(articles: List[Dict]) -> int:
    """Save basic posts (no images, just article info) to database"""
    db = await get_database()
    saved = 0
    now = datetime.utcnow()

    for article in articles:
        try:
            post_doc = {
                # Source info
                "title": article["title"],
                "content": article.get("content", ""),
                "source_url": article.get("url", ""),
                "source_name": article.get("source", "Unknown"),
                "published_date": article.get("published_date"),
                "api_tier": article.get("api_tier"),
                
                # Scores
                "confidence_score": article.get("confidence_score"),
                "relevance_score": article.get("relevance_score"),
                "ai_reasoning": article.get("ai_reasoning"),
                
                # Empty post content
                "headline": None,
                "caption": None,
                "hashtags": [],
                "full_text": None,
                
                # No images
                "background_image_path": None,
                "final_image_path": None,
                "image_url": None,
                
                # Status
                "status": "draft",
                "post_type": "basic",
                "platform": "instagram",
                
                # Metadata
                "created_at": now,
                "updated_at": now,
                "published_at": None,
            }
            
            result = await db.posts.update_one(
                {"source_url": article.get("url", "")},
                {"$setOnInsert": post_doc},
                upsert=True
            )
            
            if result.upserted_id:
                saved += 1
                
        except Exception as e:
            logger.error(f"❌ Error saving basic post: {e}")

    return saved



async def run_pipeline(top_n: int = 10, create_posts: int = 3) -> Dict:
    """
    PIPELINE FLOW:
    
    1. FETCH: Get articles from news APIs (300+ articles)
    2. CONFIDENCE FILTER: Score & filter (→ 12-15 articles)
    3. DEDUPLICATE: Remove already processed (→ 8-12 articles)
    4. VALIDATE: Check quality & relevance (→ 8-10 articles)
    5. LLM RANK: Rank by engagement potential (→ top 10)
    6. POST CREATION: Generate images for top 3 only
    7. SAVE: Only the 3 complete posts (no empty basic posts)
    
    Args:
        top_n: Number of top articles to keep (default: 10)
        create_posts: Number of complete posts to create (default: 3)
    """
    logger.info("\n" + "=" * 80)
    logger.info("🚀 BINAYAH PROPERTIES - NEWS PIPELINE STARTED")
    logger.info("=" * 80)

    try:
        # STEP 1: FETCH NEWS
        fetch_start, fetch_end = await _resolve_fetch_window()
        logger.info(f"\n📡 STEP 1: FETCHING NEWS")
        logger.info(f"  Time window: {fetch_start.isoformat()} → {fetch_end.isoformat()}")
        
        all_articles = fetch_all_news(start_time=fetch_start, end_time=fetch_end)

        if not all_articles:
            logger.info("ℹ️  No new articles found")
            await _update_fetch_window(fetch_start, fetch_end, 0, 0)
            return {
                "success": True,
                "message": "No new articles",
                "articles_fetched": 0,
                "articles_filtered": 0,
                "articles_validated": 0,
                "articles_ranked": 0,
                "complete_posts_created": 0,
                "basic_posts_saved": 0,
                "fetch_start": fetch_start.isoformat(),
                "fetch_end": fetch_end.isoformat()
            }

        logger.info(f"  ✅ Fetched: {len(all_articles)} articles")

        # STEP 2: CONFIDENCE FILTER
        logger.info(f"\n🔍 STEP 2: CONFIDENCE FILTERING")
        from app.services.newsgen.confidence_filter import ArticleConfidenceFilter
        cf = ArticleConfidenceFilter()
        filtered = cf.filter_articles(all_articles, min_score=40, target_count=15)
        logger.info(f"  ✅ Filtered: {len(filtered)} high-confidence articles")

        # STEP 3: DEDUPLICATE
        logger.info(f"\n🔄 STEP 3: DEDUPLICATION")
        unique_articles = await _deduplicate_articles(filtered)
        
        if not unique_articles:
            logger.info("ℹ️  All articles already processed")
            await _update_fetch_window(fetch_start, fetch_end, len(all_articles), 0)
            return {
                "success": True,
                "message": "No new unique articles",
                "articles_fetched": len(all_articles),
                "articles_filtered": len(filtered),
                "articles_validated": 0,
                "articles_ranked": 0,
                "complete_posts_created": 0,
                "basic_posts_saved": 0,
                "fetch_start": fetch_start.isoformat(),
                "fetch_end": fetch_end.isoformat()
            }


        # STEP 5: LLM RANKING
        logger.info(f"\n🤖 STEP 5: LLM RANKING (TOP {top_n})")
        try:
            from app.services.newsgen.ranker import ArticleRanker
            ranker = ArticleRanker()
            ranked_articles = ranker.rank_articles(unique_articles, top_n=top_n)
            logger.info(f"  ✅ Ranked: {len(ranked_articles)} top articles")
        except Exception as e:
            logger.error(f"  ❌ Ranking failed: {e}")
            logger.warning(f"  ⚠️ Falling back: using first {top_n} articles without ranking")
            # Fallback: just take first N articles
            ranked_articles = unique_articles[:top_n]
            for article in ranked_articles:
                article['relevance_score'] = 50  # Default score
        
        if not ranked_articles:
            logger.error(f"  ❌ No ranked articles available!")
            return {
                "success": False,
                "error": "Ranking returned 0 articles",
                "articles_fetched": len(all_articles),
                "articles_filtered": len(filtered),
                "articles_ranked": 0,
                "complete_posts_created": 0,
            }

        # STEP 6: CREATE COMPLETE POSTS (TOP 3)
        logger.info(f"\n🎨 STEP 6: CREATING {create_posts} COMPLETE POSTS")
        complete_posts = []  # Initialize to empty list
        try:
            complete_posts = await _generate_complete_posts(
                ranked_articles,
                num_posts=create_posts,
                platform="instagram"
            )
            logger.info(f"  ✅ Created: {len(complete_posts)} complete posts")
        except Exception as e:
            logger.error(f"  ❌ Post creation failed: {e}", exc_info=True)
            logger.warning(f"  ⚠️ Continuing with 0 complete posts")

        # STEP 7: SAVE TO DATABASE
        logger.info(f"\n💾 STEP 7: SAVING TO DATABASE")
        
        # Save complete posts (with images)
        saved_complete = 0
        if complete_posts:
            saved_complete = await _save_complete_posts(complete_posts)
            logger.info(f"  ✅ Saved: {saved_complete} complete posts")
        else:
            logger.warning(f"  ⚠️ No complete posts to save")

        saved_basic = 0

        # Update fetch window
        await _update_fetch_window(
            fetch_start,
            fetch_end,
            len(all_articles),
            saved_complete + saved_basic
        )

        logger.info("\n" + "=" * 80)
        logger.info("📊 PIPELINE SUMMARY")
        logger.info(f"  Fetched:    {len(all_articles)} articles")
        logger.info(f"  Filtered:   {len(filtered)} articles")
        logger.info(f"  Unique:     {len(unique_articles)} articles")

        logger.info(f"  Ranked:     {len(ranked_articles)} articles")
        logger.info(f"  Complete:   {saved_complete} posts (with images)")
        logger.info(f"  Basic:      {saved_basic} posts (no images)")
        logger.info(f"  Total:      {saved_complete + saved_basic} new posts")
        logger.info("=" * 80 + "\n")

        return {
            "success": True,
            "articles_fetched": len(all_articles),
            "articles_filtered": len(filtered),
            "articles_ranked": len(ranked_articles),
            "complete_posts_created": saved_complete,
            "basic_posts_saved": saved_basic,
            "total_posts": saved_complete + saved_basic,
            "fetch_start": fetch_start.isoformat(),
            "fetch_end": fetch_end.isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.error(f"❌ PIPELINE ERROR: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


def _parse_utc_iso(value: str) -> datetime:
    """Parse ISO datetime string to UTC datetime"""
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _resolve_fetch_window():
    """Determine time window for fetching news"""
    db = await get_database()
    now_utc = datetime.now(timezone.utc)
    
    state = await db[FETCH_STATE_COLLECTION].find_one({"_id": FETCH_STATE_DOC_ID})
    last_fetch_end = (state or {}).get("last_fetch_end")
    
    if isinstance(last_fetch_end, str):
        try:
            start_utc = _parse_utc_iso(last_fetch_end)
        except Exception:
            start_utc = now_utc - timedelta(days=FetcherConfig.LOOKBACK_DAYS)
    else:
        start_utc = now_utc - timedelta(days=FetcherConfig.LOOKBACK_DAYS)
    
    if start_utc > now_utc:
        start_utc = now_utc
        
    return start_utc, now_utc


async def _update_fetch_window(fetch_start, fetch_end, fetched_count, saved_count):
    """Update last fetch window in database"""
    db = await get_database()
    await db[FETCH_STATE_COLLECTION].update_one(
        {"_id": FETCH_STATE_DOC_ID},
        {
            "$set": {
                "last_fetch_start": fetch_start.isoformat(),
                "last_fetch_end": fetch_end.isoformat(),
                "last_fetched_count": fetched_count,
                "last_saved_count": saved_count,
                "updated_at": datetime.utcnow()
            }
        },
        upsert=True
    )


async def run_search_pipeline(keywords: str, max_results: int = 20) -> List[Dict]:
    """
    Instant search pipeline (for manual post creation)
    
    1. Search news by keywords
    2. Rank by relevance
    3. Return top results
    """
    logger.info(f"🔍 Search pipeline: '{keywords}'")
    
    articles = search_news(keywords, max_results=max_results)
    
    if not articles:
        return []
    
    # Rank results
    from app.services.newsgen.ranker import ArticleRanker
    ranker = ArticleRanker()
    ranked = ranker.rank_articles(articles, top_n=max_results)
    
    logger.info(f"✅ Found {len(ranked)} results")
    return ranked


async def save_news_to_db(articles: List[Dict], source: str = "trending") -> int:
    """
    Save searched/trending news into DB so frontend can reuse without re-hitting provider.
    Upserts by url.
    """
    db = await get_database()
    col = db.news_articles

    upserts = 0
    for a in articles:
        url = a.get("url") or ""
        if not url:
            continue

        doc = {
            "title": a.get("title", ""),
            "description": a.get("description", ""),
            "body": a.get("body", ""),
            "content": a.get("content", ""),
            "url": url,
            "image": a.get("image"),
            "source": a.get("source"),
            "dateTime": a.get("dateTime"),
            "date": a.get("date"),
            "fetched_source": source,
            "updated_at": datetime.utcnow(),
        }

        res = await col.update_one(
            {"url": url},
            {"$set": doc, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
        )
        if res.upserted_id is not None or res.modified_count > 0:
            upserts += 1

    return upserts

async def _store_raw_articles(articles):
    """Store raw fetched articles in news_articles collection."""
    db = await get_database()
    stored = 0
    now = datetime.utcnow()
    for a in articles:
        url = a.get("url", "")
        if not url:
            continue
        doc = {
            "title": a.get("title", ""),
            "content": a.get("content", ""),
            "url": url,
            "source": a.get("source", "Unknown"),
            "source_domain": a.get("source_domain", ""),
            "published_date": a.get("published_date", ""),
            "article_image_url": a.get("article_image_url"),
            "api_tier": a.get("api_tier", "EventRegistry"),
            "fetched_at": a.get("fetched_at", now.isoformat()),
            "updated_at": now,
        }
        result = await db.news_articles.update_one(
            {"url": url},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        if result.upserted_id is not None:
            stored += 1
    logger.info(f"  Stored {stored} new raw articles (of {len(articles)} fetched)")
    return stored


async def _auto_post_top(post_docs, count=3):
    """Auto-post top N generated posts to TEST social accounts."""
    from app.core.config import settings
    from app.services.social_publisher import SocialPublisher

    if settings.AUTO_POST_MODE == "manual":
        logger.info("  Auto-post skipped (mode=manual)")
        return {}

    publisher = SocialPublisher()
    results = {}
    for i, post in enumerate(post_docs[:count], 1):
        post_id = post.get("_id")
        image_url = post.get("image_url") or post.get("final_image_path")
        caption = post.get("caption", "")
        hashtags = post.get("hashtags", [])
        if hashtags:
            caption += "\n\n" + " ".join(f"#{h}" for h in hashtags)
        if not image_url:
            logger.warning(f"  Post {i} has no image, skipping")
            continue

        platforms = ["instagram"]
        post_results = {}
        for platform in platforms:
            try:
                result = await publisher.publish_single(
                    platform=platform, caption=caption, image_url=image_url,
                )
                post_results[platform] = result
                if result.get("success"):
                    logger.info(f"  Auto-posted #{i} to {platform}")
                else:
                    logger.warning(f"  Auto-post #{i} failed: {result.get('error')}")
            except Exception as e:
                post_results[platform] = {"success": False, "error": str(e)}

        if post_id:
            db = await get_database()
            from bson import ObjectId
            oid = ObjectId(post_id) if isinstance(post_id, str) else post_id
            any_ok = any(r.get("success") for r in post_results.values())
            update = {"auto_post_results": post_results, "updated_at": datetime.utcnow()}
            if any_ok:
                update["status"] = "posted"
                update["published_at"] = datetime.utcnow()
            await db.posts.update_one({"_id": oid}, {"$set": update})
        results[f"post_{i}"] = post_results
    return results



async def _update_progress(step: int, total: int, label: str, detail: str = "", status: str = "running"):
    """Write pipeline progress to DB for frontend polling."""
    try:
        db = await get_database()
        await db.pipeline_progress.update_one(
            {"_id": "current"},
            {"$set": {
                "step": step,
                "total_steps": total,
                "label": label,
                "detail": detail,
                "status": status,  # running | done | error
                "updated_at": datetime.utcnow(),
            }},
            upsert=True,
        )
    except Exception:
        pass  # Non-critical, don't break pipeline


# Module-level progress cache for sync scorer callback
_progress_cache = {}

def _sync_progress_update(current, total_articles, title):
    """Sync callback for LLM scorer — updates cache read by progress endpoint."""
    _progress_cache.update({
        "step": 4, "total_steps": 9,
        "label": "AI Scoring",
        "detail": f"{current}/{total_articles} scored — {title}",
        "status": "running",
    })

def get_progress_cache():
    """Read by progress endpoint for real-time scorer updates."""
    return _progress_cache


async def run_scheduled_cycle(review_minutes=60):
    """
    Automated pipeline cycle — called by APScheduler 3x/day.
    
    Steps:
      1. Fetch new articles from news APIs
      2. Store raw articles in DB
      3. Deduplicate (skip already scored/posted)
      4. LLM score + topic extraction
      5. Save scored articles
      6. Topic dedup (1 article per topic)
      7. Generate posts (image + caption)
      8. Save posts as drafts
      9. Save cycle metadata for dashboard
    """
    from app.core.config import settings
    now = datetime.utcnow

    logger.info("=" * 70)
    logger.info("PIPELINE CYCLE START")
    logger.info("=" * 70)

    try:
        await _update_progress(1, 9, "Fetching news", "Querying 6 UAE news sources...")
        # ── 1. FETCH ─────────────────────────────────────────
        fetch_start, fetch_end = await _resolve_fetch_window()
        logger.info("[1/9] FETCH  window: %s -> %s", fetch_start, fetch_end)
        all_articles = fetch_all_news(start_time=fetch_start, end_time=fetch_end)
        if not all_articles:
            logger.info("  No new articles")
            await _update_fetch_window(fetch_start, fetch_end, 0, 0)
            await _update_progress(1, 9, "Done", "No new articles found", "done")
            return {"success": True, "message": "No new articles", "articles_fetched": 0}
        logger.info("  %d articles fetched", len(all_articles))

        await _update_progress(2, 9, "Storing articles", f"{len(all_articles)} articles fetched")
        # ── 2. STORE RAW ─────────────────────────────────────
        logger.info("[2/9] STORE RAW")
        stored = await _store_raw_articles(all_articles)

        await _update_progress(3, 9, "Deduplicating", "Checking for already-scored articles...")
        # ── 3. DEDUPLICATE ───────────────────────────────────
        logger.info("[3/9] DEDUPLICATE")
        unique = await _deduplicate_articles(all_articles)
        if not unique:
            logger.info("  All articles already processed")
            await _update_fetch_window(fetch_start, fetch_end, len(all_articles), 0)
            await _update_progress(3, 9, "Done", "All articles already processed", "done")
            return {"success": True, "articles_fetched": len(all_articles), "new_articles": 0}

        # ── 4+5. LLM SCORE + INCREMENTAL SAVE ─────────────────
        # Score in batches of 25, save after each batch.
        # If Render kills the worker mid-scoring, saved batches survive
        # and dedup will skip them on the next run.
        BATCH_SIZE = 25
        await _update_progress(4, 9, "AI Scoring", f"0/{len(unique)} scored...")
        logger.info("[4/9] LLM SCORE (%d articles, batches of %d)", len(unique), BATCH_SIZE)

        scored = []
        for batch_start in range(0, len(unique), BATCH_SIZE):
            batch = unique[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(unique) + BATCH_SIZE - 1) // BATCH_SIZE

            try:
                scorer = LLMArticleScorer()
                batch_scored = scorer.score_articles(batch, on_progress=_sync_progress_update)
            except Exception as e:
                logger.error("  Batch %d scoring failed: %s", batch_num, e)
                batch_scored = batch
                for a in batch_scored:
                    a.setdefault("relevance_score", 50)
                    a.setdefault("positivity", "neutral")
                    a.setdefault("topic", "General")
                    a.setdefault("llm_reason", "Scoring unavailable")
                    a.setdefault("status", "approved")

            scored.extend(batch_scored)

            # Save this batch immediately
            saved_count = await _save_scored_articles(batch_scored)
            done = min(batch_start + BATCH_SIZE, len(unique))
            logger.info("  Batch %d/%d: scored %d, saved %d (total %d/%d)",
                        batch_num, total_batches, len(batch_scored), saved_count, done, len(unique))
            await _update_progress(4, 9, "AI Scoring", f"{done}/{len(unique)} scored, saving batch {batch_num}/{total_batches}")

        _progress_cache.clear()
        await _update_progress(5, 9, "Scores saved", f"{len(scored)} articles scored & saved")
        logger.info("[5/9] ALL SCORED & SAVED: %d articles", len(scored))

        await _update_progress(6, 9, "Topic grouping", "Removing duplicate topics...")
        # ── 6. TOPIC DEDUP ────────────────────────────────────
        logger.info("[6/9] TOPIC DEDUP")
        approved = [a for a in scored if a.get("status") == "approved"]
        topic_best = {}
        for a in approved:
            topic = (a.get("topic") or "General").strip().lower()
            if topic not in topic_best or a.get("relevance_score", 0) > topic_best[topic].get("relevance_score", 0):
                topic_best[topic] = a
        deduped = sorted(topic_best.values(), key=lambda x: x.get("relevance_score", 0), reverse=True)
        logger.info("  %d approved -> %d unique topics", len(approved), len(deduped))

        # ── 7. GENERATE POSTS ─────────────────────────────────
        gen_count = settings.POSTS_TO_GENERATE
        top_articles = deduped[:gen_count]
        await _update_progress(7, 9, "Generating posts", f"Creating {len(top_articles)} posts with images...")
        logger.info("[7/9] GENERATE %d POSTS", len(top_articles))
        complete_posts = []
        if top_articles:
            try:
                complete_posts = await _generate_complete_posts(
                    top_articles, num_posts=len(top_articles), platform="instagram"
                )
            except Exception as e:
                logger.error("  Generation failed: %s", e, exc_info=True)

        await _update_progress(8, 9, "Saving posts", f"{len(complete_posts)} posts created")
        # ── 8. SAVE POSTS ─────────────────────────────────────
        logger.info("[8/9] SAVE %d POSTS", len(complete_posts))
        saved_posts = 0
        if complete_posts:
            saved_posts = await _save_complete_posts(complete_posts)

        await _update_progress(9, 9, "Finalizing", "Saving cycle metadata...")
        # ── 9. SAVE CYCLE METADATA ────────────────────────────
        logger.info("[9/9] SAVE CYCLE METADATA")
        db = await get_database()
        post_ids = []
        if complete_posts:
            for cp in complete_posts:
                doc = await db.posts.find_one({"source_url": cp.get("url", "")}, {"_id": 1})
                if doc:
                    post_ids.append(str(doc["_id"]))

        cycle_meta = {
            "cycle_id": now().strftime("%Y%m%d_%H%M"),
            "started_at": now(),
            "auto_post_at": now() + timedelta(minutes=review_minutes),
            "status": "pending_review",
            "articles_fetched": len(all_articles),
            "articles_scored": len(scored),
            "articles_approved": len(approved),
            "topics_unique": len(deduped),
            "posts_generated": saved_posts,
            "post_ids": post_ids,
        }
        await db.pipeline_cycles.insert_one(cycle_meta)
        logger.info("  Cycle %s: %d posts, review until %s",
                     cycle_meta["cycle_id"], saved_posts,
                     cycle_meta["auto_post_at"].strftime("%H:%M UTC"))

        await _update_fetch_window(fetch_start, fetch_end, len(all_articles), saved_posts)

        summary = {
            "success": True,
            "articles_fetched": len(all_articles),
            "articles_stored": stored,
            "articles_scored": len(scored),
            "articles_approved": len(approved),
            "topics_unique": len(deduped),
            "posts_generated": saved_posts,
        }
        await _update_progress(9, 9, "Done", f"{saved_posts} posts ready for review", "done")
        logger.info("CYCLE DONE: %s", summary)
        return summary

    except Exception as e:
        await _update_progress(0, 9, "Error", str(e)[:100], "error")
        logger.error("CYCLE ERROR: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
