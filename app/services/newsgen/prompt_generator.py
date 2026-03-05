"""
AI-Powered Prompt Generation System

Uses OpenAI GPT to generate high-quality, contextual prompts for:
1. Background images (Stability AI)
2. Avatar images (Stability AI)
3. Enhanced headlines
4. Identifying green keywords

This is intelligent, context-aware prompts tailored to each news story.
"""

from __future__ import annotations
import os
import json
from typing import Dict, List, Set
from dataclasses import dataclass

import openai
from openai import OpenAI
from app.services.newsgen.ingest import NewsItem


@dataclass
class PromptResult:
    """Result from AI prompt generation"""
    background_prompt: str
    avatar_prompt: str
    enhanced_headline: str
    green_keywords: Set[str]
    template_suggestion: str  # "professional", "meme", or "statistic"


class PromptGenerator:
    """
    Generates contextual prompts using OpenAI GPT.
    """
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not found")
        # Don't set openai.api_key globally - we'll pass it per request
        self.client = openai.OpenAI(api_key=self.api_key)
    
    def generate_prompts(self, story: NewsItem) -> PromptResult:
        """
        Generate all prompts for a news story in one call.
        This is more efficient than multiple separate calls.
        """
        
        system_prompt = """You are an expert social media content creator specializing in crypto and finance news.
                            Your task is to generate image prompts and content for viral social media posts.

                            GUIDELINES:
                            - Background images should be dramatic, cinematic, and eye-catching
                            - Avatars should match the story context (trader, CEO, analyst, etc.)
                            - Headlines should be punchy, under 16 words, and use active voice
                            - Green keywords are important crypto/finance terms to highlight
                            - Template choice: "professional" for serious news, "meme" for viral content, "statistic" for data-heavy news
                            """

        user_prompt = f"""Generate social media content for this crypto/finance news:

                            TITLE: {story.title}
                            SUMMARY: {story.summary}
                            SOURCE: {story.source}

                            Please respond with a JSON object containing:
                            {{
                            "background_prompt": "Detailed prompt for Stability AI to generate background image (cinematic, dramatic, no text, 1:1 aspect ratio)",
                            "avatar_prompt": "Detailed prompt for Stability AI to generate avatar/person image (professional headshot, 1:1 aspect ratio)",
                            "enhanced_headline": "Rewritten headline optimized for social media (max 16 words, punchy, active voice)",
                            "green_keywords": ["KEYWORD1", "KEYWORD2", ...],  // 3-7 important terms to highlight in green
                            "template_suggestion": "professional|meme|statistic"  // which template style fits best
                            }}

                            IMPORTANT RULES:
                            1. Background prompt must include: "no text, no logos, no watermark, 1:1 aspect ratio, cinematic lighting"
                            2. Avatar prompt must include: "professional headshot, centered face, blurred background, 1:1 aspect ratio"
                            3. Green keywords should be: crypto terms, dollar amounts, company names, key metrics
                            4. Enhanced headline must be under 16 words
                            5. All prompts must be specific and detailed (50-100 words each)
                            """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=800,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            return PromptResult(
                background_prompt=result.get("background_prompt", ""),
                avatar_prompt=result.get("avatar_prompt", ""),
                enhanced_headline=result.get("enhanced_headline", story.title),
                green_keywords=set(result.get("green_keywords", [])),
                template_suggestion=result.get("template_suggestion", "professional")
            )
            
        except Exception as e:
            # Fallback to simple prompts if AI fails
            print(f"⚠️ OpenAI prompt generation failed: {e}")
            return self._fallback_prompts(story)
    
    def _fallback_prompts(self, story: NewsItem) -> PromptResult:
        """
        Fallback to simple keyword-based prompts if OpenAI fails.
        """
        import re
        
        # Extract keywords
        words = re.findall(r"[A-Za-z0-9$]+", story.title)
        bad = {"THE", "A", "AN", "AND", "OR", "TO", "OF", "IN", "ON", "FOR", "WITH", "AS", "BY"}
        keywords = [w.upper() for w in words if len(w) >= 3 and w.upper() not in bad]
        keywords = keywords[:6]
        kw_str = " ".join(keywords)
        
        # Simple green keywords detection
        green_kw = set()
        for w in keywords:
            if any(x in w for x in ["BTC", "ETH", "XRP", "SOL", "$", "BILLION", "MILLION", "ETF"]):
                green_kw.add(w)
        
        return PromptResult(
            background_prompt=(
                f"Cinematic crypto news poster background, dramatic moody lighting, "
                f"dark storm clouds, high contrast, premium photoreal style, "
                f"visual themes: {kw_str}, "
                f"subtle crypto finance symbolism in background, "
                f"clean composition with empty lower third for text, "
                f"no text, no logos, no watermark, 1:1 aspect ratio."
            ),
            avatar_prompt=(
                f"Photoreal studio headshot portrait of confident financial analyst, "
                f"mid 30s-40s, professional attire, neutral expression, sharp focus, "
                f"clean rim light, blurred newsroom background, "
                f"topic: {kw_str[:50]}, "
                f"centered face with headroom for circular crop, "
                f"no text, no watermark, 1:1 aspect ratio."
            ),
            enhanced_headline=story.title[:100],
            green_keywords=green_kw or {"BTC", "ETH", "CRYPTO"},
            template_suggestion="professional"
        )


# Singleton instance
_generator = None

def get_prompt_generator() -> PromptGenerator:
    """Get or create the singleton prompt generator"""
    global _generator
    if _generator is None:
        _generator = PromptGenerator()
    return _generator


def generate_prompts_for_story(story: NewsItem) -> PromptResult:
    """
    Convenience function to generate all prompts for a story.
    
    Usage:
        
        prompts = generate_prompts_for_story(news_item)
        print(prompts.background_prompt)
        print(prompts.avatar_prompt)
        print(prompts.enhanced_headline)
    """
    generator = get_prompt_generator()
    return generator.generate_prompts(story)


def build_background_prompt(story: NewsItem) -> str:
    """Backward compatible - generates background prompt only"""
    prompts = generate_prompts_for_story(story)
    return prompts.background_prompt


def build_avatar_prompt(story: NewsItem) -> str:
    """Backward compatible - generates avatar prompt only"""
    prompts = generate_prompts_for_story(story)
    return prompts.avatar_prompt
