import json
from openai import AsyncOpenAI
from typing import Dict
from app.core.config import settings
from app.db.models import NewsCategory

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def validate_and_score_article(article: Dict) -> Dict:
    prompt = f"""You are a real estate news analyst for Dubai. Rate this article on a scale of 1-10 for relevance to Dubai real estate market.

                Article Title: {article['title']}
                Article Content: {article['content'][:1000]}
                Source: {article['source']}

                Return JSON with:
                - score: 1-10 (10 = highly relevant to Dubai real estate)
                - category: one of [laws_regulations, innovations, updates, new_projects, new_launches]
                - reasoning: brief explanation (max 100 chars)
                - headline: optimized headline for social media (max 80 chars)
                - keywords: 3-5 key phrases from article

                Criteria for high scores (8-10):
                - Direct Dubai real estate news
                - Government announcements, regulations, or initiatives
                - Major project launches or completions
                - Market updates and statistics
                - Developer news in Dubai

                Low scores (1-4):
                - Not about Dubai or UAE
                - Not about real estate
                - Generic real estate advice
                - International news unrelated to Dubai

                Return only valid JSON, no other text."""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a real estate news analyst. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        return {
            'ai_score': float(result.get('score', 0)),
            'category': result.get('category', 'updates'),
            'ai_reasoning': result.get('reasoning', ''),
            'optimized_headline': result.get('headline', article['title']),
            'keywords': result.get('keywords', [])
        }
        
    except Exception as e:
        return {
            'ai_score': 0,
            'category': 'updates',
            'ai_reasoning': f'Error: {str(e)}',
            'optimized_headline': article['title'],
            'keywords': []
        }


async def generate_post_content(article: Dict) -> Dict:
    prompt = f"""Create social media content for this Dubai real estate article:

                Title: {article['title']}
                Content: {article['content'][:800]}

                Generate:
                1. headline: Attention-grabbing headline (max 60 chars, use keywords from article)
                2. caption: Instagram/X caption (max 200 chars, engaging, informative)
                3. hashtags: 5 relevant hashtags (Dubai real estate focused)
                4. image_prompt: Description for AI image generation (max 100 chars, describe Dubai real estate scene)

                Return only valid JSON."""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a social media content creator for Dubai real estate. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        return {
            'headline': result.get('headline', article['title'])[:60],
            'caption': result.get('caption', '')[:200],
            'hashtags': result.get('hashtags', [])[:5],
            'image_prompt': result.get('image_prompt', 'Dubai real estate skyline')[:100]
        }
        
    except Exception as e:
        return {
            'headline': article['title'][:60],
            'caption': article['content'][:200],
            'hashtags': ['#Dubai', '#RealEstate', '#DubaiProperty', '#UAE', '#Investment'],
            'image_prompt': 'Modern Dubai skyline with luxury properties'
        }


async def score_all_drafts():
    from app.db.session import get_database
    from app.db.models import PostStatus
    
    db = await get_database()
    
    drafts = await db.posts.find({"status": PostStatus.DRAFT, "ai_score": {"$exists": False}}).to_list(None)
    
    scored_count = 0
    for draft in drafts:
        article = {
            'title': draft.get('title', ''),
            'content': draft.get('content', ''),
            'source': draft.get('source_name', '')
        }
        
        validation = await validate_and_score_article(article)
        
        await db.posts.update_one(
            {"_id": draft["_id"]},
            {"$set": validation}
        )
        
        scored_count += 1
    
    return {"posts_scored": scored_count}
