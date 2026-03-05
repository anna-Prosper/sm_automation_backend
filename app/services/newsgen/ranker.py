"""
LLM ARTICLE RANKER
Rank all fetched articles and select top 10 for posting
"""

import os
import logging
from typing import List, Dict
import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

class ArticleRanker:
    """Rank articles using OpenAI GPT-4"""
    
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-4o-mini"
    
    def rank_articles(self, articles: List[Dict], top_n: int = 10) -> List[Dict]:
        """
        Rank articles by relevance to Dubai real estate
        Returns top N articles with scores
        """
        if not articles:
            logger.warning("No articles to rank!")
            return []
            
        if len(articles) <= top_n:
            logger.info(f"Only {len(articles)} articles, no ranking needed")
            # Assign default scores
            for article in articles:
                article.setdefault('relevance_score', 50)
            return articles
        
        logger.info(f"\n{'='*70}")
        logger.info(f"🤖 RANKING {len(articles)} ARTICLES WITH LLM")
        logger.info(f"{'='*70}")
        
        # Score each article
        scored_articles = []
        api_failures = 0
        
        for i, article in enumerate(articles, 1):
            try:
                score = self._score_article(article)
                article['relevance_score'] = score
                scored_articles.append(article)
                logger.info(f"  [{i}/{len(articles)}] {article.get('source', 'Unknown')}: {score}/100")
            except Exception as e:
                api_failures += 1
                logger.error(f"  [{i}/{len(articles)}] Error scoring: {e}")
                article['relevance_score'] = 50  # Default score instead of 0
                scored_articles.append(article)
        
        # If ALL articles failed to score, return them anyway
        if api_failures == len(articles):
            logger.warning(f"⚠️ ALL {api_failures} articles failed to score! Returning all with default scores.")
            logger.warning("Check: 1) OpenAI API key, 2) API rate limits, 3) Network connectivity")
        
        # Sort by score (highest first)
        ranked = sorted(scored_articles, key=lambda x: x.get('relevance_score', 0), reverse=True)
        
        # Get top N
        top_articles = ranked[:top_n]
        
        logger.info(f"\n📊 RANKING COMPLETE")
        logger.info(f"Top {top_n} selected (API failures: {api_failures}/{len(articles)}):")
        for i, article in enumerate(top_articles, 1):
            score = article.get('relevance_score', 0)
            title = article.get('title', 'Untitled')[:50]
            source = article.get('source', 'Unknown')
            logger.info(f"  {i}. [{score}/100] {source}: {title}...")
        
        return top_articles
    
    def _score_article(self, article: Dict) -> int:
        """
        Score a single article (0-100)
        Higher = more relevant to Dubai luxury real estate
        """
        prompt = f"""You are a content quality expert for Binayah Properties, a luxury real estate company in Dubai.

                    Rate this article on a scale of 0-100 based on:
                    1. Relevance to Dubai/UAE real estate (40 points)
                    2. Focus on residential, luxury, or investment topics (30 points)
                    3. Recency and newsworthiness (15 points)
                    4. Content quality and depth (15 points)

                    Article:
                    Title: {article['title']}
                    Source: {article['source']}
                    Content: {article['content'][:500]}...

                    Return ONLY a number from 0-100. No explanation."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.3
            )
            
            score_text = response.choices[0].message.content.strip()
            score = int(''.join(filter(str.isdigit, score_text)))
            return min(max(score, 0), 100)
            
        except Exception as e:
            logger.error(f"Error scoring article: {e}")
            return 50 


#Testin
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    # Test articles
    test_articles = [
        {
            "title": "Dubai property prices surge 20% in Q1 2026",
            "content": "Dubai real estate market shows strong growth...",
            "source": "Gulf News"
        },
        {
            "title": "New metro line announced",
            "content": "Dubai RTA announces new metro expansion...",
            "source": "Khaleej Times"
        },
        {
            "title": "Luxury villas in Palm Jumeirah sell out in 48 hours",
            "content": "High-end properties continue to attract investors...",
            "source": "Arabian Business"
        }
    ]
    
    ranker = ArticleRanker()
    ranked = ranker.rank_articles(test_articles, top_n=2)
    
    print("\n✅ Ranking test complete")