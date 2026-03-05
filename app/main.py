from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import html
import sys
import traceback

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, select_autoescape

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.db.session import get_database
from app.routes import dashboard, health, news_routes, newsgen, posts, templates, publishing, media, auth, articles

BASE_DIR = Path(__file__).resolve().parent
PREVIEW_PAGE_PATH = BASE_DIR / "static" / "news_preview.html"
PREVIEW_TEMPLATE = Environment(
    autoescape=select_autoescape(default_for_string=True),
).from_string(PREVIEW_PAGE_PATH.read_text(encoding="utf-8"))

def _resolve_project_root(base_dir: Path) -> Path:
    """
    Find project root by walking upward for `.env`.
    """
    for candidate in [base_dir, *base_dir.parents]:
        if (candidate / ".env").exists():
            return candidate
    return base_dir.parent


PROJECT_ROOT = _resolve_project_root(BASE_DIR)
load_dotenv(PROJECT_ROOT / ".env")

PREVIEW_FETCH_STATE_COLLECTION = "news_fetch_state"
PREVIEW_FETCH_STATE_DOC_PREFIX = "eventregistry_preview_window"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scheduler import start_scheduler
    start_scheduler()
    yield


app = FastAPI(
    title="Binayah News Automation API",
    version="1.0.0",
    lifespan=lifespan,
)


_cors_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "https://sm-automation-frontend.vercel.app",
]
_frontend_url = getattr(settings, "FRONTEND_URL", None)
if _frontend_url:
    _cors_origins.append(_frontend_url.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(newsgen.router, prefix="/api/newsgen", tags=["newsgen"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(dashboard.router)
app.include_router(publishing.router)
app.include_router(auth.router)
app.include_router(articles.router)
app.include_router(news_routes.router, prefix="/api/news", tags=["news"])
app.include_router(media.router, prefix="/api/media", tags=["media"])

# Serve generated images from local storage
import os as _os
_storage_path = _os.getenv("STORAGE_LOCAL_PATH", "/app/storage")
_os.makedirs(_storage_path, exist_ok=True)
app.mount("/storage", StaticFiles(directory=_storage_path), name="storage")


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


def _parse_utc_iso(value: str) -> datetime:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_fetch_window_time(value: str) -> str:
    try:
        dt = _parse_utc_iso(value)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return value


def _preview_state_doc_id(source_domain: str) -> str:
    return f"{PREVIEW_FETCH_STATE_DOC_PREFIX}:{source_domain}"


async def _get_preview_fetch_states(source_domains: List[str]) -> Dict[str, Dict]:
    db = await get_database()
    if not source_domains:
        return {}

    doc_ids = [_preview_state_doc_id(domain) for domain in source_domains]
    states_by_domain: Dict[str, Dict] = {}
    cursor = db[PREVIEW_FETCH_STATE_COLLECTION].find({"_id": {"$in": doc_ids}})
    async for doc in cursor:
        source_domain = doc.get("source_domain")
        if not source_domain:
            source_domain = str(doc.get("_id", "")).split(":", 1)[-1]
        if source_domain:
            states_by_domain[source_domain] = doc
    return states_by_domain


async def _resolve_preview_fetch_windows(
    sources: List[Dict],
    lookback_days: int,
) -> Dict[str, Tuple[datetime, datetime]]:
    now_utc = datetime.now(timezone.utc)
    source_domains = [source["domain"] for source in sources]
    states_by_domain = await _get_preview_fetch_states(source_domains)

    windows: Dict[str, Tuple[datetime, datetime]] = {}
    for source in sources:
        source_domain = source["domain"]
        last_fetch_end = states_by_domain.get(source_domain, {}).get("last_fetch_end")

        if isinstance(last_fetch_end, str):
            try:
                start_utc = _parse_utc_iso(last_fetch_end)
            except Exception:
                start_utc = now_utc - timedelta(days=lookback_days)
        else:
            start_utc = now_utc - timedelta(days=lookback_days)

        if start_utc > now_utc:
            start_utc = now_utc

        windows[source_domain] = (start_utc, now_utc)

    return windows


async def _update_preview_fetch_windows(
    windows: Dict[str, Tuple[datetime, datetime]],
    source_name_by_domain: Dict[str, str],
    fetched_counts_by_domain: Dict[str, int],
    saved_counts_by_domain: Dict[str, int],
) -> None:
    db = await get_database()
    now = datetime.utcnow()

    for source_domain, (fetch_start, fetch_end) in windows.items():
        await db[PREVIEW_FETCH_STATE_COLLECTION].update_one(
            {"_id": _preview_state_doc_id(source_domain)},
            {
                "$set": {
                    "source_domain": source_domain,
                    "source_name": source_name_by_domain.get(source_domain, source_domain),
                    "last_fetch_start": fetch_start.isoformat(),
                    "last_fetch_end": fetch_end.isoformat(),
                    "last_fetched_count": fetched_counts_by_domain.get(source_domain, 0),
                    "last_saved_count": saved_counts_by_domain.get(source_domain, 0),
                    "updated_at": now,
                }
            },
            upsert=True,
        )


def _get_combined_fetch_window(states_by_domain: Dict[str, Dict]) -> Tuple[Optional[str], Optional[str]]:
    starts: List[datetime] = []
    ends: List[datetime] = []

    for state in states_by_domain.values():
        start_raw = state.get("last_fetch_start")
        end_raw = state.get("last_fetch_end")

        if isinstance(start_raw, str):
            try:
                starts.append(_parse_utc_iso(start_raw))
            except Exception:
                pass

        if isinstance(end_raw, str):
            try:
                ends.append(_parse_utc_iso(end_raw))
            except Exception:
                pass

    if not starts or not ends:
        return None, None

    start_display = _format_fetch_window_time(min(starts).isoformat())
    end_display = _format_fetch_window_time(max(ends).isoformat())
    return start_display, end_display


async def _save_articles_to_db(articles: List[Dict]) -> Tuple[int, Dict[str, int]]:
    db = await get_database()
    saved_count = 0
    saved_counts_by_domain: Dict[str, int] = {}
    now = datetime.utcnow()

    for article in articles:
        title = (article.get("title") or "").strip()
        source_url = (article.get("url") or "").strip()
        source_domain = (article.get("source_domain") or "").strip() or "unknown"
        if not title or not source_url:
            continue

        post_doc = {
            "title": title,
            "content": (article.get("content") or "").strip()[:2000],
            "source_url": source_url,
            "source_name": article.get("source", "Unknown"),
            "published_date": article.get("published_date"),
            "api_tier": article.get("api_tier", "EventRegistry"),
            "headline": None,
            "caption": None,
            "hashtags": [],
            "background_image_path": None,
            "final_image_path": None,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "published_at": None,
        }

        result = await db.posts.update_one(
            {"source_url": source_url},
            {"$setOnInsert": post_doc},
            upsert=True,
        )
        if result.upserted_id:
            saved_count += 1
            saved_counts_by_domain[source_domain] = saved_counts_by_domain.get(source_domain, 0) + 1

    return saved_count, saved_counts_by_domain


async def _load_articles_from_db(limit: int = 500) -> List[Dict]:
    db = await get_database()
    rows = await db.posts.find(
        {},
        {
            "title": 1,
            "content": 1,
            "source_url": 1,
            "source_name": 1,
            "published_date": 1,
            "api_tier": 1,
            "created_at": 1,
        },
    ).sort("created_at", -1).limit(limit).to_list(limit)

    articles: List[Dict] = []
    for row in rows:
        published_date = row.get("published_date")
        article = {
            "title": row.get("title", ""),
            "content": row.get("content", ""),
            "url": row.get("source_url", ""),
            "source": row.get("source_name", "Unknown"),
            "published_date": published_date,
            "api_tier": row.get("api_tier", "EventRegistry"),
            "release_time": _format_release_time(published_date or ""),
        }
        articles.append(article)

    articles.sort(key=lambda a: _published_ts(a.get("published_date", "")), reverse=True)
    return articles


def _build_source_stats(articles: List[Dict], configured_sources: List[Dict]) -> Tuple[List[Dict], List[str], int]:
    counts: Dict[str, int] = {}
    tiers: Dict[str, str] = {}

    for article in articles:
        source = article.get("source", "Unknown")
        counts[source] = counts.get(source, 0) + 1
        if source not in tiers:
            tiers[source] = article.get("api_tier", "EventRegistry")

    configured_names = [src["name"] for src in configured_sources]
    extra_names = sorted(name for name in counts if name not in configured_names)
    ordered_names = configured_names + extra_names

    max_count = max((counts.get(name, 0) for name in ordered_names), default=1) or 1
    source_stats: List[Dict] = []
    source_names: List[str] = []
    configured_hits = 0

    for name in ordered_names:
        count = counts.get(name, 0)
        if count > 0:
            source_names.append(name)
            if name in configured_names:
                configured_hits += 1
        tier = tiers.get(name, "—") if count > 0 else "—"
        source_stats.append(
            {
                "name": name,
                "count": count,
                "tier": tier,
                "tier_class": _tier_class(tier),
                "bar_pct": max(int((count / max_count) * 100), 8) if count > 0 else 0,
            }
        )

    return source_stats, source_names, configured_hits


async def _run_preview_fetch() -> None:
    from app.services.newsgen.multi_api_fetcher import Config, fetch_news_by_source_windows

    windows_by_domain = await _resolve_preview_fetch_windows(Config.SOURCES, Config.LOOKBACK_DAYS)
    fetched_by_source = fetch_news_by_source_windows(source_windows=windows_by_domain)

    all_articles: List[Dict] = []
    fetched_counts_by_domain: Dict[str, int] = {}
    for source_domain, rows in fetched_by_source.items():
        fetched_counts_by_domain[source_domain] = len(rows)
        all_articles.extend(rows)

    unique_by_url: Dict[str, Dict] = {}
    for article in all_articles:
        source_url = (article.get("url") or "").strip()
        if not source_url:
            continue
        if source_url not in unique_by_url:
            unique_by_url[source_url] = article

    deduped_articles = list(unique_by_url.values())
    _, saved_counts_by_domain = await _save_articles_to_db(deduped_articles)

    source_name_by_domain = {source["domain"]: source["name"] for source in Config.SOURCES}
    await _update_preview_fetch_windows(
        windows=windows_by_domain,
        source_name_by_domain=source_name_by_domain,
        fetched_counts_by_domain=fetched_counts_by_domain,
        saved_counts_by_domain=saved_counts_by_domain,
    )


async def _render_preview_page() -> HTMLResponse:
    try:
        from app.services.newsgen.multi_api_fetcher import Config

        articles = await _load_articles_from_db()
        source_stats, source_names, sources_with_results = _build_source_stats(articles, Config.SOURCES)
        api_tiers_used = set(a.get("api_tier", "unknown") for a in articles)

        source_domains = [source["domain"] for source in Config.SOURCES]
        preview_states_by_domain = await _get_preview_fetch_states(source_domains)
        fetch_window_start, fetch_window_end = _get_combined_fetch_window(preview_states_by_domain)

        now = datetime.utcnow()
        date_from = (now - timedelta(days=Config.LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")

        rendered = PREVIEW_TEMPLATE.render(
            articles=articles,
            total_articles=len(articles),
            total_sources=len(Config.SOURCES),
            sources_with_results=sources_with_results,
            api_count=len(api_tiers_used),
            source_stats=source_stats,
            source_names=source_names,
            lookback_days=Config.LOOKBACK_DAYS,
            date_from=date_from,
            date_to=date_to,
            fetch_window_start=fetch_window_start,
            fetch_window_end=fetch_window_end,
            has_fetch_history=bool(fetch_window_start and fetch_window_end),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return HTMLResponse(content=rendered)
    except Exception as exc:
        error_html = f"""
        <html>
        <body style="font-family: Arial; padding: 50px; background: #ffebee;">
            <h1 style="color: #c62828;">Error Loading Articles</h1>
            <p style="font-size: 1.2em;">{html.escape(str(exc))}</p>
            <pre style="background: #fff; padding: 15px; border-radius: 8px; margin-top: 15px; overflow-x: auto;">{html.escape(traceback.format_exc())}</pre>
            <button onclick="location.reload()" style="padding: 10px 20px; font-size: 1.1em; margin-top: 20px; cursor: pointer;">
                Retry
            </button>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=500)


@app.get("/", include_in_schema=False)
async def preview_page_get():
    return await _render_preview_page()


@app.post("/", include_in_schema=False)
async def preview_page_post():
    await _run_preview_fetch()
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        app_dir=str(Path(__file__).resolve().parents[1]),
    )
