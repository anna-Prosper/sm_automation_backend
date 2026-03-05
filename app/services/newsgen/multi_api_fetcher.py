"""
EVENT REGISTRY NEWS FETCHER (PER SOURCE)
Each source is fetched separately with source-specific query conditions.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


def _resolve_newsapi_key() -> Optional[str]:
    """
    Resolve API key from process env first, then app settings as fallback.
    This keeps local `python apps/api/app/main.py` runs working even when
    `.env` is parsed by pydantic-settings instead of exported to os.environ.
    """
    env_key = os.getenv("NEWSAPI_KEY")
    if env_key:
        return env_key

    try:
        from app.core.config import settings

        return settings.NEWSAPI_KEY
    except Exception:
        return None


class Config:
    NEWSAPI_KEY = _resolve_newsapi_key()
    NEWSAPI_URL = "https://eventregistry.org/api/v1/article/getArticles"
    LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "7"))
    MAX_ITEMS_PER_SOURCE = int(os.getenv("NEWS_MAX_ITEMS_PER_SOURCE", "100"))
    REAL_ESTATE_CONCEPT_URI = "http://en.wikipedia.org/wiki/Real_estate"
    UAE_LOCATION_URI = "http://en.wikipedia.org/wiki/United_Arab_Emirates"

    # Per-source query behavior based on provided query examples.
    SOURCES = [
        {"name": "Gulf News", "domain": "gulfnews.com", "include_location": True},
        {"name": "Khaleej times", "domain": "khaleejtimes.com"},
        {"name": "Gulf Business", "domain": "gulfbusiness.com"},
        {"name": "Zawya", "domain": "zawya.com", "include_concept": True, "include_location": True},
        {"name": "Trade Arabia", "domain": "tradearabia.com", "include_concept": True},
        {"name": "Emirates 24/7", "domain": "emirates247.com"},
    ]


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_range(start_time: Optional[datetime], end_time: Optional[datetime]) -> Tuple[str, str]:
    resolved_end = _to_utc(end_time or datetime.now(timezone.utc))
    resolved_start = _to_utc(start_time or (resolved_end - timedelta(days=Config.LOOKBACK_DAYS)))

    if resolved_start > resolved_end:
        resolved_start = resolved_end

    return resolved_start.date().isoformat(), resolved_end.date().isoformat()


def _build_source_query(
    source: Dict,
    date_start: str,
    date_end: str,
    keyword_filter: Optional[str] = None,
) -> Dict:
    query_parts: List[Dict] = []

    if source.get("include_concept"):
        query_parts.append({"conceptUri": Config.REAL_ESTATE_CONCEPT_URI})

    if source.get("include_location"):
        query_parts.append({"locationUri": Config.UAE_LOCATION_URI})

    query_parts.append({"sourceUri": source["domain"]})
    query_parts.append({"dateStart": date_start, "dateEnd": date_end})

    if keyword_filter:
        kw = keyword_filter.strip()
        # Use the full phrase as a required keyword match (not split into
        # individual tokens via $or, which makes single generic words like
        # "investment" match everything).
        query_parts.append({"keyword": kw, "keywordLoc": "title,body"})

    return {"$query": {"$and": query_parts}}


def _parse_article(item: Dict, source: Dict, fetched_at: str) -> Optional[Dict]:
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    if not title or not url:
        return None

    body = (item.get("body") or item.get("summary") or "").strip()
    
    image_url = None
    if item.get("image"):
        image_url = item["image"]
    elif item.get("images") and isinstance(item["images"], list) and len(item["images"]) > 0:
        image_url = item["images"][0]

    return {
        "title": title,
        "content": body[:2000],
        "url": url,
        "source": source["name"],
        "source_domain": source["domain"],
        "published_date": item.get("dateTime", "") or item.get("date", ""),
        "fetched_at": fetched_at,
        "api_tier": "EventRegistry",
        "article_image_url": image_url,
    }


def _fetch_source_news(
    source: Dict,
    date_start: str,
    date_end: str,
    max_items: int,
    keyword_filter: Optional[str] = None,
) -> List[Dict]:
    query = _build_source_query(
        source=source,
        date_start=date_start,
        date_end=date_end,
        keyword_filter=keyword_filter,
    )

    payload = {
        "apiKey": _resolve_newsapi_key(),
        "query": query,
        "articlesCount": max_items,
        "articlesSortBy": "date",
        "includeArticleTitle": True,
        "includeArticleBody": True,
        "includeSourceTitle": True,
        "includeArticleUrl": True,
        "includeArticlePublishingDate": True,
        "includeArticleImage": True,
    }

    logger.info(
        f"[EventRegistry] {source['domain']} | {date_start} -> {date_end} | maxItems={max_items}"
    )

    try:
        response = requests.post(Config.NEWSAPI_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.error(f"[EventRegistry] Request failed for {source['domain']}: {exc}")
        return []
    except ValueError:
        logger.error(f"[EventRegistry] Invalid JSON for {source['domain']}")
        return []

    results = data.get("articles", {}).get("results", [])
    fetched_at = datetime.now(timezone.utc).isoformat()
    parsed: List[Dict] = []

    for item in results:
        parsed_item = _parse_article(item=item, source=source, fetched_at=fetched_at)
        if parsed_item:
            parsed.append(parsed_item)

    logger.info(f"[EventRegistry] {source['domain']} -> {len(parsed)} parsed articles")
    return parsed


def _dedupe_by_url(articles: List[Dict]) -> List[Dict]:
    seen = set()
    unique_rows: List[Dict] = []

    for article in articles:
        url = article.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        unique_rows.append(article)

    return unique_rows


def fetch_news_by_source_windows(
    source_windows: Dict[str, Tuple[datetime, datetime]],
    max_items_per_source: Optional[int] = None,
    keyword_filter: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """
    Fetch configured sources with independent time windows per source.
    Returns mapping: source_domain -> fetched rows.
    """
    if not _resolve_newsapi_key():
        logger.warning("[EventRegistry] No NEWSAPI_KEY configured")
        return {}

    per_source_limit = max_items_per_source or Config.MAX_ITEMS_PER_SOURCE
    rows_by_source: Dict[str, List[Dict]] = {}

    for source in Config.SOURCES:
        source_domain = source["domain"]
        source_window = source_windows.get(source_domain)
        if not source_window:
            continue

        start_time, end_time = source_window
        date_start, date_end = _date_range(start_time=start_time, end_time=end_time)
        rows_by_source[source_domain] = _fetch_source_news(
            source=source,
            date_start=date_start,
            date_end=date_end,
            max_items=per_source_limit,
            keyword_filter=keyword_filter,
        )

    return rows_by_source


def fetch_all_news(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    max_items_per_source: Optional[int] = None,
    keyword_filter: Optional[str] = None,
) -> List[Dict]:
    """
    Fetch all configured sources separately using source-specific query logic.
    """
    if not _resolve_newsapi_key():
        logger.warning("[EventRegistry] No NEWSAPI_KEY configured")
        return []

    date_start, date_end = _date_range(start_time=start_time, end_time=end_time)
    per_source_limit = max_items_per_source or Config.MAX_ITEMS_PER_SOURCE

    all_articles: List[Dict] = []
    for source in Config.SOURCES:
        source_rows = _fetch_source_news(
            source=source,
            date_start=date_start,
            date_end=date_end,
            max_items=per_source_limit,
            keyword_filter=keyword_filter,
        )
        all_articles.extend(source_rows)

    unique_articles = _dedupe_by_url(all_articles)
    logger.info(
        f"[EventRegistry] Total parsed={len(all_articles)} | unique_by_url={len(unique_articles)}"
    )
    return unique_articles


def _fetch_broad_keyword_search(
    keywords: str,
    date_start: str,
    date_end: str,
    max_items: int = 50,
) -> List[Dict]:
    """
    Search EventRegistry across ALL sources (not limited to Config.SOURCES).
    Uses keyword as the primary filter without sourceUri constraint.
    This finds articles about niche topics (e.g. "Bahrain investment")
    that our 6 configured sources may not cover.
    """
    api_key = _resolve_newsapi_key()
    if not api_key:
        return []

    query = {
        "$query": {
            "$and": [
                {"keyword": keywords, "keywordLoc": "title,body"},
                {"dateStart": date_start, "dateEnd": date_end},
                {"lang": "eng"},
            ]
        }
    }

    payload = {
        "apiKey": api_key,
        "query": query,
        "articlesCount": max_items,
        "articlesSortBy": "rel",  # Sort by relevance for keyword searches
        "includeArticleTitle": True,
        "includeArticleBody": True,
        "includeSourceTitle": True,
        "includeArticleUrl": True,
        "includeArticlePublishingDate": True,
        "includeArticleImage": True,
    }

    logger.info(f"[EventRegistry] Broad keyword search: '{keywords}' | {date_start} -> {date_end}")

    try:
        response = requests.post(Config.NEWSAPI_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.error(f"[EventRegistry] Broad search request failed: {exc}")
        return []
    except ValueError:
        logger.error(f"[EventRegistry] Broad search invalid JSON")
        return []

    results = data.get("articles", {}).get("results", [])
    fetched_at = datetime.now(timezone.utc).isoformat()
    parsed: List[Dict] = []

    # Use a generic source dict for articles from unknown sources
    for item in results:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            continue

        body = (item.get("body") or item.get("summary") or "").strip()
        source_info = item.get("source") or {}
        source_name = source_info.get("title", "Unknown") if isinstance(source_info, dict) else str(source_info)
        source_domain = source_info.get("uri", "") if isinstance(source_info, dict) else ""

        image_url = None
        if item.get("image"):
            image_url = item["image"]
        elif item.get("images") and isinstance(item["images"], list) and len(item["images"]) > 0:
            image_url = item["images"][0]

        parsed.append({
            "title": title,
            "content": body[:2000],
            "url": url,
            "source": source_name,
            "source_domain": source_domain,
            "published_date": item.get("dateTime", "") or item.get("date", ""),
            "fetched_at": fetched_at,
            "api_tier": "EventRegistry",
            "article_image_url": image_url,
        })

    logger.info(f"[EventRegistry] Broad search -> {len(parsed)} articles")
    return parsed


def _keyword_relevance_score(article: Dict, keywords: str) -> float:
    """
    Score how relevant an article is to the search keywords.
    Returns 0.0-1.0.  Title matches are weighted 3× more than body matches.
    Articles that score 0 should be discarded.
    """
    kw_lower = keywords.lower()
    tokens = [t for t in kw_lower.replace(",", " ").split() if len(t) >= 2]
    if not tokens:
        return 1.0  # no keywords to filter on

    title = (article.get("title") or "").lower()
    content = (article.get("content") or "").lower()
    text = f"{title} {content}"

    # Full phrase match is strongest signal
    full_phrase_in_title = 1.0 if kw_lower in title else 0.0
    full_phrase_in_body = 0.5 if kw_lower in content else 0.0

    # Count how many individual tokens appear anywhere
    token_hits = sum(1 for t in tokens if t in text)
    token_ratio = token_hits / len(tokens) if tokens else 0

    # Title token matches are more important
    title_token_hits = sum(1 for t in tokens if t in title)
    title_ratio = title_token_hits / len(tokens) if tokens else 0

    score = (
        full_phrase_in_title * 0.35
        + full_phrase_in_body * 0.15
        + title_ratio * 0.25
        + token_ratio * 0.25
    )

    return round(min(score, 1.0), 3)


def search_news(keywords: str, max_results: int = 30) -> List[Dict]:
    """
    Search Event Registry with strict keyword matching.
    Combines source-specific + broad keyword search, then filters by relevance.
    """
    logger.info(f"[EventRegistry] Search keywords: '{keywords}'")

    per_source_limit = min(max(max_results, 1), Config.MAX_ITEMS_PER_SOURCE)

    # Strategy 1: Search within configured sources
    source_rows = fetch_all_news(
        max_items_per_source=per_source_limit,
        keyword_filter=keywords,
    )

    # Strategy 2: Broad keyword search across all sources
    date_start, date_end = _date_range(start_time=None, end_time=None)
    broad_rows = _fetch_broad_keyword_search(
        keywords=keywords,
        date_start=date_start,
        date_end=date_end,
        max_items=max_results * 2,
    )

    # Combine, dedupe, score, and filter
    combined = _dedupe_by_url(source_rows + broad_rows)

    scored = []
    for article in combined:
        score = _keyword_relevance_score(article, keywords)
        if score > 0.05:  # drop completely irrelevant articles
            article["_relevance"] = score
            scored.append(article)

    # Sort by relevance (best matches first)
    scored.sort(key=lambda a: a.get("_relevance", 0), reverse=True)

    logger.info(
        f"[EventRegistry] Search '{keywords}': "
        f"source_hits={len(source_rows)}, broad_hits={len(broad_rows)}, "
        f"after_filter={len(scored)}"
    )

    # Clean up internal field before returning
    for a in scored:
        a.pop("_relevance", None)

    return scored[:max_results]


def search_news_for_frontend(
    keywords: str, 
    max_results: int = 10,
    days_back: int = 30
) -> List[Dict]:
    """
    Search news with frontend-friendly formatting.
    
    Combines source-specific + broad keyword search, scores by relevance,
    and returns only articles that actually match the user's keywords.
    
    Returns articles formatted for React CreatePostModal:
    - title: Article headline
    - body: Full article content
    - description: Truncated preview (300 chars)
    - url: Article URL
    - image: Article image URL (if available)
    - source: {title, uri}
    - dateTime: ISO timestamp
    - date: Date string
    
    Args:
        keywords: Search keywords (e.g., "Dubai Marina luxury apartments")
        max_results: Maximum number of results (default: 10)
        days_back: How many days back to search (default: 30)
    
    Returns:
        List of formatted article dictionaries
    """
    if not _resolve_newsapi_key():
        logger.warning("[EventRegistry] No NEWSAPI_KEY configured")
        return []
    
    logger.info(f"[EventRegistry] Frontend search: '{keywords}' (max={max_results}, days={days_back})")
    
    # Calculate time range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)
    date_start, date_end = _date_range(start_time=start_time, end_time=end_time)
    
    # Strategy 1: Search within configured sources (uses sourceUri filter)
    source_articles = fetch_all_news(
        start_time=start_time,
        end_time=end_time,
        max_items_per_source=max(max_results // len(Config.SOURCES), 3),
        keyword_filter=keywords,
    )

    # Strategy 2: Broad keyword search across ALL sources (no sourceUri)
    broad_articles = _fetch_broad_keyword_search(
        keywords=keywords,
        date_start=date_start,
        date_end=date_end,
        max_items=max_results * 3,
    )

    # Combine and deduplicate
    combined = _dedupe_by_url(source_articles + broad_articles)

    # Score by keyword relevance and drop irrelevant articles
    scored = []
    for article in combined:
        score = _keyword_relevance_score(article, keywords)
        if score > 0.05:
            article["_relevance"] = score
            scored.append(article)

    # Sort by relevance (best keyword matches first)
    scored.sort(key=lambda a: a.get("_relevance", 0), reverse=True)

    logger.info(
        f"[EventRegistry] Search '{keywords}': "
        f"source_hits={len(source_articles)}, broad_hits={len(broad_articles)}, "
        f"after_filter={len(scored)}"
    )

    # Format for frontend
    formatted_articles = []
    for article in scored[:max_results]:
        full_content = article.get("content", "")
        
        description = full_content[:300] + "..." if len(full_content) > 300 else full_content
        
        formatted_articles.append({
            "title": article.get("title", ""),
            "body": full_content,
            "description": description, 
            "url": article.get("url", ""),
            "image": article.get("article_image_url"),
            "source": {
                "title": article.get("source", "Unknown"),
                "uri": article.get("source_domain", "")
            },
            "dateTime": article.get("published_date", ""),
            "date": article.get("published_date", "")[:10] if article.get("published_date") else "",
        })
    
    logger.info(f"[EventRegistry] Returning {len(formatted_articles)} formatted articles")
    return formatted_articles


def fetch_real_estate_news(
    days_back: int = 7,
    max_results: int = 20,
) -> List[Dict]:
    """
    Fetch ONLY real estate news using EventRegistry's concept URI filter.
    Uses conceptUri for "Real estate" + locationUri for UAE across all
    configured sources.  No keyword needed — the concept filter ensures
    every returned article is classified as real-estate by EventRegistry.

    Args:
        days_back: How many days back to search
        max_results: Maximum number of articles

    Returns:
        List of articles (raw internal format)
    """
    api_key = _resolve_newsapi_key()
    if not api_key:
        logger.warning("[EventRegistry] No NEWSAPI_KEY configured")
        return []

    logger.info(f"[EventRegistry] Fetching real-estate-only news (days={days_back})")

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)
    date_start, date_end = _date_range(start_time=start_time, end_time=end_time)

    query = {
        "$query": {
            "$and": [
                {"conceptUri": Config.REAL_ESTATE_CONCEPT_URI},
                {"locationUri": Config.UAE_LOCATION_URI},
                {"dateStart": date_start, "dateEnd": date_end},
                {"lang": "eng"},
            ]
        }
    }

    payload = {
        "apiKey": api_key,
        "query": query,
        "articlesCount": max_results * 2,  # fetch extra, will filter
        "articlesSortBy": "date",
        "includeArticleTitle": True,
        "includeArticleBody": True,
        "includeSourceTitle": True,
        "includeArticleUrl": True,
        "includeArticlePublishingDate": True,
        "includeArticleImage": True,
    }

    try:
        response = requests.post(Config.NEWSAPI_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.error(f"[EventRegistry] Real estate fetch failed: {exc}")
        return []
    except ValueError:
        logger.error(f"[EventRegistry] Real estate fetch invalid JSON")
        return []

    results = data.get("articles", {}).get("results", [])
    fetched_at = datetime.now(timezone.utc).isoformat()
    parsed: List[Dict] = []

    for item in results:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            continue

        body = (item.get("body") or item.get("summary") or "").strip()
        source_info = item.get("source") or {}
        source_name = source_info.get("title", "Unknown") if isinstance(source_info, dict) else str(source_info)
        source_domain = source_info.get("uri", "") if isinstance(source_info, dict) else ""

        image_url = None
        if item.get("image"):
            image_url = item["image"]
        elif item.get("images") and isinstance(item["images"], list) and len(item["images"]) > 0:
            image_url = item["images"][0]

        parsed.append({
            "title": title,
            "content": body[:2000],
            "url": url,
            "source": source_name,
            "source_domain": source_domain,
            "published_date": item.get("dateTime", "") or item.get("date", ""),
            "fetched_at": fetched_at,
            "api_tier": "EventRegistry",
            "article_image_url": image_url,
        })

    unique = _dedupe_by_url(parsed)
    logger.info(f"[EventRegistry] Real estate only -> {len(unique)} articles")
    return unique[:max_results]


def search_real_estate_for_frontend(
    days_back: int = 7,
    max_results: int = 20,
) -> List[Dict]:
    """
    Fetch real-estate-only news formatted for the React frontend.
    Same output shape as search_news_for_frontend.
    """
    articles = fetch_real_estate_news(days_back=days_back, max_results=max_results)

    formatted = []
    for article in articles:
        full_content = article.get("content", "")
        description = full_content[:300] + "..." if len(full_content) > 300 else full_content

        formatted.append({
            "title": article.get("title", ""),
            "body": full_content,
            "description": description,
            "url": article.get("url", ""),
            "image": article.get("article_image_url"),
            "source": {
                "title": article.get("source", "Unknown"),
                "uri": article.get("source_domain", ""),
            },
            "dateTime": article.get("published_date", ""),
            "date": article.get("published_date", "")[:10] if article.get("published_date") else "",
        })

    logger.info(f"[EventRegistry] Real estate frontend -> {len(formatted)} articles")
    return formatted


def get_trending_topics(days_back: int = 7, max_results: int = 20) -> List[Dict]:
    """
    Get trending Dubai real estate news (no keyword filter).
    
    Useful for discovering what's currently newsworthy.
    
    Args:
        days_back: How many days back to look
        max_results: Maximum number of articles
    
    Returns:
        List of recent articles sorted by date
    """
    logger.info(f"[EventRegistry] Fetching trending topics (days={days_back})")
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)
    
    articles = fetch_all_news(
        start_time=start_time,
        end_time=end_time,
        max_items_per_source=max(max_results // len(Config.SOURCES), 3),
        keyword_filter=None
    )
    
    return articles[:max_results]


def search_by_location(
    location: str,
    max_results: int = 10,
    days_back: int = 30
) -> List[Dict]:
    """
    Search for news about specific Dubai locations.
    
    Examples:
        - "Dubai Marina"
        - "Downtown Dubai"
        - "Palm Jumeirah"
        - "Business Bay"
    
    Args:
        location: Dubai location/area name
        max_results: Maximum results
        days_back: Days back to search
    
    Returns:
        List of articles about that location
    """
    keywords = f"{location} Dubai real estate"
    return search_news_for_frontend(
        keywords=keywords,
        max_results=max_results,
        days_back=days_back
    )


def search_by_developer(
    developer: str,
    max_results: int = 10,
    days_back: int = 30
) -> List[Dict]:
    """
    Search for news about specific developers.
    
    Examples:
        - "Emaar"
        - "Damac"
        - "Nakheel"
        - "Dubai Properties"
    
    Args:
        developer: Developer name
        max_results: Maximum results
        days_back: Days back to search
    
    Returns:
        List of articles about that developer
    """
    keywords = f"{developer} Dubai"
    return search_news_for_frontend(
        keywords=keywords,
        max_results=max_results,
        days_back=days_back
    )


def get_source_statistics(articles: List[Dict]) -> Dict[str, int]:
    """
    Count articles by source.
    
    Useful for debugging and understanding source distribution.
    """
    stats = {}
    for article in articles:
        source = article.get("source", "Unknown")
        stats[source] = stats.get(source, 0) + 1
    
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    # Test 1: Original fetch
    print("\n=== TEST 1: Original Fetch ===")
    rows = fetch_all_news()
    print(f"Fetched {len(rows)} articles")
    
    # Test 2: Keyword search
    print("\n=== TEST 2: Keyword Search ===")
    results = search_news_for_frontend("Dubai Marina luxury apartments", max_results=5)
    print(f"Found {len(results)} articles matching keywords")
    for i, article in enumerate(results, 1):
        print(f"{i}. {article['title'][:60]}... ({article['source']['title']})")
    
    # Test 3: Source statistics
    print("\n=== TEST 3: Source Statistics ===")
    stats = get_source_statistics(rows)
    for source, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source}: {count} articles")