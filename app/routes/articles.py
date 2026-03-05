"""
Scored Articles API - exposes all LLM-scored articles for the dashboard.
Supports filtering by status, positivity, relevance range, and search.
"""

import logging
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Query
from app.db.session import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get("/scored")
async def get_scored_articles(
    status: Optional[str] = Query(None, description="approved|rejected"),
    positivity: Optional[str] = Query(None, description="positive|negative|neutral"),
    min_relevance: Optional[int] = Query(None, ge=0, le=100),
    max_relevance: Optional[int] = Query(None, ge=0, le=100),
    search: Optional[str] = Query(None),
    sort_by: str = Query("relevance_score", description="relevance_score|scored_at|positivity"),
    sort_order: str = Query("desc", description="asc|desc"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return all LLM-scored articles with optional filters."""
    db = await get_database()
    query = {}

    if status:
        query["status"] = status
    if positivity:
        query["positivity"] = positivity
    if min_relevance is not None or max_relevance is not None:
        rel_filter = {}
        if min_relevance is not None:
            rel_filter["$gte"] = min_relevance
        if max_relevance is not None:
            rel_filter["$lte"] = max_relevance
        query["relevance_score"] = rel_filter
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"llm_reason": {"$regex": search, "$options": "i"}},
            {"source": {"$regex": search, "$options": "i"}},
        ]

    sort_dir = -1 if sort_order == "desc" else 1

    cursor = db.scored_articles.find(query).sort(sort_by, sort_dir).skip(skip).limit(limit)
    articles = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        articles.append(doc)

    total = await db.scored_articles.count_documents(query)

    # Aggregate stats
    stats = {}
    pipeline_stats = [
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "approved": {"$sum": {"$cond": [{"$eq": ["$status", "approved"]}, 1, 0]}},
            "rejected": {"$sum": {"$cond": [{"$eq": ["$status", "rejected"]}, 1, 0]}},
            "positive": {"$sum": {"$cond": [{"$eq": ["$positivity", "positive"]}, 1, 0]}},
            "negative": {"$sum": {"$cond": [{"$eq": ["$positivity", "negative"]}, 1, 0]}},
            "neutral":  {"$sum": {"$cond": [{"$eq": ["$positivity", "neutral"]}, 1, 0]}},
            "avg_relevance": {"$avg": "$relevance_score"},
        }}
    ]

    async for s in db.scored_articles.aggregate(pipeline_stats):
        stats = {
            "total": s.get("total", 0),
            "approved": s.get("approved", 0),
            "rejected": s.get("rejected", 0),
            "positive": s.get("positive", 0),
            "negative": s.get("negative", 0),
            "neutral": s.get("neutral", 0),
            "avg_relevance": round(s.get("avg_relevance", 0), 1),
        }

    return {
        "articles": articles,
        "total": total,
        "skip": skip,
        "limit": limit,
        "stats": stats,
    }


@router.patch("/scored/{article_id}/status")
async def update_article_status(article_id: str, status: str = Query(...)):
    """Manually override an article status (approve/reject)."""
    from bson import ObjectId
    db = await get_database()
    result = await db.scored_articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}},
    )
    if result.modified_count == 0:
        return {"ok": False, "error": "Article not found"}
    return {"ok": True, "status": status}


@router.delete("/scored/clear")
async def clear_scored_articles():
    """Clear all scored articles (for fresh pipeline runs)."""
    db = await get_database()
    result = await db.scored_articles.delete_many({})
    return {"ok": True, "deleted": result.deleted_count}


@router.get("/raw")
async def get_raw_articles(
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_database()
    query = {}
    if source:
        query["source"] = {"$regex": source, "$options": "i"}
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"source": {"$regex": search, "$options": "i"}},
        ]
    cursor = db.news_articles.find(query).sort("fetched_at", -1).skip(skip).limit(limit)
    articles = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        articles.append(doc)
    total = await db.news_articles.count_documents(query)
    pipeline_agg = [{"$group": {"_id": "$source", "count": {"$sum": 1}}}]
    sources = {}
    async for s in db.news_articles.aggregate(pipeline_agg):
        sources[s["_id"]] = s["count"]
    return {"articles": articles, "total": total, "skip": skip, "limit": limit, "sources": sources}
