"""
CONFIDENCE-BASED ARTICLE FILTER
Hard filter to reduce 100+ articles to 10-15 best candidates
NO LLM - Pure algorithmic scoring based on industry-specific rules
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class ArticleConfidenceFilter:
    """
    Filters articles based on confidence score calculated from multiple factors:
    - Keyword relevance (Dubai real estate specific)
    - Source credibility
    - Content quality metrics
    - Recency
    - Title quality
    """
    
    KEYWORDS = {
        # High Value Keywords (15 points each)
        "high_value": [
            "dubai real estate", "property prices", "off-plan", "residential",
            "villa", "apartment", "penthouse", "luxury property",
            "downtown dubai", "dubai marina", "palm jumeirah",
            "emaar", "damac", "nakheel", "developer launch",
            "master plan", "mega project", "investment opportunity"
        ],
        
        # Medium Value Keywords (10 points each)
        "medium_value": [
            "property market", "rental yield", "roi", "capital appreciation",
            "mortgage", "home loan", "property law", "regulation",
            "dubai land department", "rera", "ready property",
            "handover", "completion", "occupancy", "tenant",
            "real estate news", "market report", "price trend"
        ],
        
        # Low Value Keywords (5 points each)
        "low_value": [
            "real estate", "property", "housing", "residential market",
            "commercial property", "retail space", "office space",
            "investment", "buyer", "seller", "broker", "agent",
            "transaction", "sale", "purchase", "rent"
        ],
        
        # Negative Keywords (deduct points)
        "negative": [
            "crisis", "crash", "bubble", "scam", "fraud",
            "lawsuit", "dispute", "illegal", "foreclosure",
            "abandoned", "ghost town", "overpriced", "unfinished"
        ]
    }
    
    # Source Reliability Scores (0-100)
    SOURCE_SCORES = {
        "Gulf News": 95,
        "Khaleej Times": 95,
        "The National News": 90,
        "Arabian Business": 90,
        "Gulf Business": 85,
        "Zawya": 85,
        "Trade Arabia": 80,
        "Emirates 24/7": 75,
        "default": 50  # Unknown sources
    }
    
    # Minimum scores
    MIN_CONFIDENCE_SCORE = 60  # Articles below this are rejected
    TARGET_ARTICLES = 12  # Aim for 12 articles (will be reduced to 10 by LLM ranker)
    
    def calculate_confidence_score(self, article: Dict) -> float:
        """
        Calculate confidence score (0-100) for an article
        
        Components:
        - Keyword Relevance: 0-40 points
        - Source Credibility: 0-25 points
        - Content Quality: 0-20 points
        - Recency: 0-10 points
        - Title Quality: 0-5 points
        """
        score = 0.0
        
        score += self._score_keywords(article)
        
        score += self._score_source(article)
        
        score += self._score_content_quality(article)
        
        score += self._score_recency(article)
        
        score += self._score_title(article)
        
        return min(100.0, max(0.0, score))
    
    def _score_keywords(self, article: Dict) -> float:
        """Score based on keyword presence (0-40 points)"""
        text = f"{article.get('title', '')} {article.get('content', '')}".lower()
        score = 0.0
        
        # High value keywords
        high_matches = sum(1 for kw in self.KEYWORDS["high_value"] if kw.lower() in text)
        score += min(30.0, high_matches * 15)
        
        # Medium value keywords
        medium_matches = sum(1 for kw in self.KEYWORDS["medium_value"] if kw.lower() in text)
        score += min(20.0, medium_matches * 10)
        
        # Low value keywords
        low_matches = sum(1 for kw in self.KEYWORDS["low_value"] if kw.lower() in text)
        score += min(10.0, low_matches * 5)
        
        # Negative keywords
        negative_matches = sum(1 for kw in self.KEYWORDS["negative"] if kw.lower() in text)
        score -= negative_matches * 10
        
        return max(0.0, min(40.0, score))
    
    def _score_source(self, article: Dict) -> float:
        """Score based on source credibility (0-25 points)"""
        source = article.get('source', '')
        base_score = self.SOURCE_SCORES.get(source, self.SOURCE_SCORES["default"])

        return (base_score / 100) * 25
    
    def _score_content_quality(self, article: Dict) -> float:
        """Score based on content quality indicators (0-20 points)"""
        content = article.get('content', '')
        title = article.get('title', '')
        score = 0.0

        content_length = len(content)
        if content_length > 1000:
            score += 8
        elif content_length > 500:
            score += 6
        elif content_length > 200:
            score += 4
        elif content_length > 100:
            score += 2
        
        # Has numbers/statistics
        if re.search(r'\d{1,3}[,.]?\d*\s*(million|billion|percent|%|AED|USD|\$)', content, re.I):
            score += 4
        
        # Has quotes
        if '"' in content or '"' in content or '"' in content:
            score += 3
        
        # Title length
        title_words = len(title.split())
        if 8 <= title_words <= 20:
            score += 3
        elif 6 <= title_words <= 25:
            score += 1.5
        
        # Has proper structure
        if content.count('.') >= 3:
            score += 2
        
        return min(20.0, score)
    
    def _score_recency(self, article: Dict) -> float:
        """Score based on how recent the article is (0-10 points)"""
        try:
            pub_date_str = article.get('published_date', '')
            if not pub_date_str:
                return 5.0
            
            # Parse date
            pub_date = self._parse_date(pub_date_str)
            if not pub_date:
                return 5.0
            
            # Calculate age in days
            age_days = (datetime.utcnow() - pub_date).days
            
            if age_days <= 1:
                return 10.0
            elif age_days <= 3:
                return 8.0
            elif age_days <= 7:
                return 6.0
            elif age_days <= 14:
                return 4.0
            elif age_days <= 30:
                return 2.0
            else:
                return 0.0
                
        except Exception as e:
            logger.warning(f"Error scoring recency: {e}")
            return 5.0
    
    def _score_title(self, article: Dict) -> float:
        """Score based on title quality (0-5 points)"""
        title = article.get('title', '')
        score = 0.0
        
        # Has location
        if re.search(r'\b(dubai|uae|abu dhabi|sharjah)\b', title, re.I):
            score += 2
        
        # Has action words (1.5 points)
        action_words = ['launches', 'announces', 'unveils', 'opens', 'completes', 'plans', 'introduces']
        if any(word in title.lower() for word in action_words):
            score += 1.5
        
        # Has numbers
        if re.search(r'\d+', title):
            score += 1
        
        # Not clickbait
        clickbait_words = ['shocking', 'amazing', 'unbelievable', 'you won\'t believe']
        if not any(word in title.lower() for word in clickbait_words):
            score += 0.5
        
        return min(5.0, score)
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string to datetime object"""
        formats = [
            '%Y-%m-%d',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S.%fZ',
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str[:19], fmt[:19])
            except:
                continue
        
        return None
    
    def filter_articles(
        self, 
        articles: List[Dict], 
        min_score: Optional[float] = None,
        target_count: Optional[int] = None
    ) -> List[Dict]:
        """
        Filter and rank articles by confidence score
        
        Args:
            articles: List of article dictionaries
            min_score: Minimum confidence score (default: MIN_CONFIDENCE_SCORE)
            target_count: Target number of articles (default: TARGET_ARTICLES)
        
        Returns:
            Filtered and sorted list of articles with confidence scores
        """
        min_score = min_score or self.MIN_CONFIDENCE_SCORE
        target_count = target_count or self.TARGET_ARTICLES
        
        logger.info(f"Filtering {len(articles)} articles...")
        
        # Calculate confidence score for each article
        scored_articles = []
        for article in articles:
            score = self.calculate_confidence_score(article)
            article['confidence_score'] = round(score, 2)
            
            if score >= min_score:
                scored_articles.append(article)
        
        # Sort by confidence score
        scored_articles.sort(key=lambda x: x['confidence_score'], reverse=True)
        
        # Take top N articles
        filtered = scored_articles[:target_count]
        
        logger.info(f"Filtered to {len(filtered)} articles (scores: {min_score}+)")
        
        # Log score distribution
        if filtered:
            scores = [a['confidence_score'] for a in filtered]
            logger.info(f"Score range: {min(scores):.1f} - {max(scores):.1f}")
            logger.info(f"Average score: {sum(scores)/len(scores):.1f}")
        
        return filtered


def analyze_article_quality(article: Dict) -> Dict:
    """
    Analyze an article and return detailed quality metrics
    Useful for debugging and understanding why an article scored high/low
    """
    filter = ArticleConfidenceFilter()
    
    return {
        "overall_score": filter.calculate_confidence_score(article),
        "keyword_score": filter._score_keywords(article),
        "source_score": filter._score_source(article),
        "quality_score": filter._score_content_quality(article),
        "recency_score": filter._score_recency(article),
        "title_score": filter._score_title(article)
    }


def print_article_analysis(article: Dict):
    """Print detailed analysis of an article"""
    analysis = analyze_article_quality(article)
    
    print(f"\n{'='*70}")
    print(f"ARTICLE ANALYSIS: {article.get('title', 'N/A')[:60]}...")
    print(f"{'='*70}")
    print(f"Overall Score:    {analysis['overall_score']:.1f}/100")
    print(f"├─ Keywords:      {analysis['keyword_score']:.1f}/40")
    print(f"├─ Source:        {analysis['source_score']:.1f}/25")
    print(f"├─ Quality:       {analysis['quality_score']:.1f}/20")
    print(f"├─ Recency:       {analysis['recency_score']:.1f}/10")
    print(f"└─ Title:         {analysis['title_score']:.1f}/5")
    print(f"{'='*70}\n")



#Testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    sample_articles = [
        {
            "title": "Emaar launches off-plan luxury villas in Dubai Marina",
            "content": "Emaar Properties has announced the launch of a new residential project featuring luxury villas in Dubai Marina. The project, valued at AED 2.5 billion, will include 150 waterfront villas with prices ranging from AED 8 million to AED 25 million. \"This is a landmark development,\" said the CEO.",
            "source": "Gulf News",
            "published_date": "2026-02-12T10:00:00"
        },
        {
            "title": "Dubai property market shows growth",
            "content": "The real estate market in Dubai continues to expand with new developments.",
            "source": "Unknown Source",
            "published_date": "2026-01-15T10:00:00"
        },
        {
            "title": "Major scandal in real estate sector",
            "content": "Authorities uncover illegal property transactions in crisis-hit development.",
            "source": "Gulf News",
            "published_date": "2026-02-10T10:00:00"
        }
    ]
    
    filter = ArticleConfidenceFilter()

    for article in sample_articles:
        print_article_analysis(article)

    filtered = filter.filter_articles(sample_articles, min_score=40, target_count=10)
    
    print(f"\nFiltered Articles: {len(filtered)}")
    for i, article in enumerate(filtered, 1):
        print(f"{i}. [{article['confidence_score']}/100] {article['title']}")
