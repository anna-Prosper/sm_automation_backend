"""
LLM ARTICLE SCORER
Scores ALL fetched articles using GPT for:
  - relevance (0-100)
  - positivity (positive / negative / neutral)
  - reason (1-2 sentence explanation)
  - status (approved / rejected based on thresholds)
"""

import os
import json
import re
import logging
from typing import List, Dict

from openai import OpenAI

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 40
MAX_ARTICLE_WORDS = 2000


def _truncate_to_words(text, max_words=MAX_ARTICLE_WORDS):
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


class LLMArticleScorer:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-4o-mini"

    def score_articles(self, articles, on_progress=None):
        if not articles:
            return []
        logger.info(f"Scoring {len(articles)} articles with LLM")
        # Quality gate: skip empty/tiny articles
        filtered = []
        skipped = 0
        for a in articles:
            text = a.get("content") or a.get("body") or a.get("description") or ""
            title = a.get("title") or ""
            if len(title) < 10 or len(text.split()) < 30:
                a["relevance_score"] = 0
                a["positivity"] = "neutral"
                a["llm_reason"] = "Skipped: insufficient content"
                a["status"] = "rejected"
                skipped += 1
            else:
                filtered.append(a)
        if skipped:
            logger.info(f"  Quality gate: skipped {skipped} articles (empty/too short)")

        scored = []
        total = len(filtered)
        for i, article in enumerate(filtered, 1):
            try:
                result = self._score_single(article)
                article["relevance_score"] = result["relevance_score"]
                article["positivity"] = result["positivity"]
                article["llm_reason"] = result["llm_reason"]
                article["topic"] = result.get("topic", "General")
                if result["relevance_score"] < RELEVANCE_THRESHOLD:
                    article["status"] = "rejected"
                elif result["positivity"] == "negative":
                    article["status"] = "rejected"
                else:
                    article["status"] = "approved"
                scored.append(article)
                logger.info(f"  [{i}/{total}] {article['status'].upper():8s} rel={result['relevance_score']:3d} pos={result['positivity']:8s} | {article.get('title', '')[:55]}")
                # Report progress every 5 articles
                if on_progress and (i % 5 == 0 or i == total):
                    on_progress(i, total, article.get("title", "")[:50])
            except Exception as e:
                logger.error(f"  [{i}/{len(articles)}] Scoring error: {e}")
                article.setdefault("relevance_score", 50)
                article.setdefault("positivity", "neutral")
                article.setdefault("llm_reason", f"Scoring failed: {e}")
                article.setdefault("status", "approved")
                scored.append(article)
        scored.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)
        approved = [a for a in scored if a.get("status") == "approved"]
        scored.extend([a for a in articles if a.get("llm_reason", "").startswith("Skipped")])
        scored.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)
        approved = [a for a in scored if a.get("status") == "approved"]
        logger.info(f"Scoring complete: {len(approved)} approved / {len(scored) - len(approved)} rejected (incl {skipped} pre-filtered)")
        return scored

    def _score_single(self, article):
        title = article.get("title", "Untitled")
        raw_text = article.get("content") or article.get("body") or article.get("description") or ""
        text = _truncate_to_words(raw_text, MAX_ARTICLE_WORDS)
        source = article.get("source", "Unknown")
        prompt = f"""You are a content quality expert for Binayah Properties, a luxury real estate company in Dubai, UAE.

Evaluate this article and return a JSON object with exactly these fields:
- "relevance_score": integer 0-100 (how relevant to Dubai/UAE real estate, property market, luxury living, construction, investment)
- "positivity": one of "positive", "negative", "neutral" (overall tone/sentiment for a real estate company social media)
- "topic": 2-4 word topic label for grouping similar articles (e.g. "Dubai Marina Launch", "Off-Plan Sales", "Visa Policy Update")
- "reason": 1-2 sentence explanation of your scoring

Scoring guide for relevance:
  90-100: Directly about Dubai/UAE real estate (new launches, price trends, developer news, regulations)
  70-89:  Related to UAE property (infrastructure, lifestyle, tourism impact on property)
  50-69:  Tangentially related (GCC economy, regional investment, expat life)
  30-49:  Loosely related (global real estate trends, construction tech)
  0-29:   Not relevant (unrelated news)

Article:
Title: {title}
Source: {source}
Text: {text}

Return ONLY valid JSON, no markdown fences, no extra text."""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM JSON: {raw[:200]}")
            score_match = re.search(r'"?relevance_score"?\s*:\s*(\d+)', raw)
            score = int(score_match.group(1)) if score_match else 50
            return {"relevance_score": min(max(score, 0), 100), "positivity": "neutral", "llm_reason": f"Parse error, extracted score={score}"}
        return {
            "relevance_score": min(max(int(data.get("relevance_score", 50)), 0), 100),
            "positivity": data.get("positivity", "neutral").lower().strip(),
            "topic": data.get("topic", "General"),
            "llm_reason": data.get("reason", "No reason provided"),
        }
