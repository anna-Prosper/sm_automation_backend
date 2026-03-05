import hashlib
from typing import List, Dict
from datetime import datetime, timedelta
from app.db.session import get_database


def hash_url(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def calculate_similarity(text1: str, text2: str) -> float:
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union)


async def check_duplicate_url(url: str) -> bool:
    db = await get_database()
    
    url_hash = hash_url(url)
    
    existing = await db.posts.find_one({"source_url_hash": url_hash})
    
    return existing is not None


async def check_duplicate_title(title: str, threshold: float = 0.8) -> bool:
    db = await get_database()
    
    recent_cutoff = datetime.utcnow() - timedelta(days=30)
    
    recent_posts = await db.posts.find({
        "created_at": {"$gte": recent_cutoff}
    }).to_list(None)
    
    for post in recent_posts:
        similarity = calculate_similarity(title, post.get('title', ''))
        if similarity >= threshold:
            return True
    
    return False


async def filter_duplicates(articles: List[Dict]) -> List[Dict]:
    unique_articles = []
    
    for article in articles:
        is_duplicate_url = await check_duplicate_url(article['url'])
        
        if is_duplicate_url:
            continue
        
        is_duplicate_title = await check_duplicate_title(article['title'])
        
        if is_duplicate_title:
            continue
        
        article['source_url_hash'] = hash_url(article['url'])
        unique_articles.append(article)
    
    return unique_articles


async def cleanup_old_hashes(days: int = 30):
    db = await get_database()
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    result = await db.posts.delete_many({
        "created_at": {"$lt": cutoff},
        "status": "rejected"
    })
    
    return result.deleted_count
