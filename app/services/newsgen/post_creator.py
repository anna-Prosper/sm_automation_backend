"""
POST CREATOR - CAPTIONS, HASHTAGS & FINAL POST ASSEMBLY
Generates professional social media posts for Dubai real estate content
"""

import os
import logging
import json
import re
from typing import Dict, List, Optional, Literal
from openai import OpenAI

logger = logging.getLogger(__name__)


PLATFORM_SPECS = {
    "instagram": {
        "max_caption_chars": 2200,
        "max_hashtags": 20,
        "has_hashtags": True,
    },
    "facebook": {
        "max_caption_chars": 600,
        "max_hashtags": 8,
        "has_hashtags": True,
    },
    "twitter": {
        "max_caption_chars": 245,
        "max_hashtags": 3,
        "has_hashtags": True,
    },
    "whatsapp": {
        "max_caption_chars": 300,
        "max_hashtags": 0,
        "has_hashtags": False,
    },
    "threads": {
        "max_caption_chars": 480,
        "max_hashtags": 5,
        "has_hashtags": True,
    },
    "linkedin": {
        "max_caption_chars": 3000,
        "max_hashtags": 5,
        "has_hashtags": True,
    },
}


class MultiPlatformPostCreator:
    """
    Generates human-sounding, platform-native content for all 5 platforms
    in a single OpenAI call. Also generates 3 carousel slide angles.
    """

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY") or ""
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = "gpt-4o-mini"

    def create_all_platforms(self, article: Dict) -> Dict[str, Dict]:
        """
        Generate content for instagram, facebook, twitter, whatsapp, threads, linkedin
        from a single article in one API call.

        Returns dict keyed by platform name, each value has:
            caption: str
            hashtags: List[str]  (empty list for whatsapp)
        """
        try:
            raw = self._call_openai_all_platforms(article)
            result = self._parse_and_validate_platforms(raw, article)
            logger.info(f"All-platform content generated for: {article['title'][:50]}")
            return result
        except Exception as e:
            logger.error(f"Multi-platform generation failed: {e}")
            return self._fallback_all_platforms(article)

    def create_carousel_angles(self, article: Dict, num_slides: Optional[int] = None) -> List[Dict]:
        """
        Generate carousel slide angles for an article.

        Args:
            article:    Source article dict.
            num_slides: Exact number of slides to create.
                        If None, the AI decides based on article richness (3–8 range).

        Returns list of dicts, each with:
            slide_number: int
            headline: str
            slide_caption: str
            angle_label: str
        """
        try:
            raw = self._call_openai_carousel(article, num_slides=num_slides)
            slides = self._parse_carousel(raw, article, num_slides=num_slides)
            logger.info(f"Carousel angles generated ({len(slides)} slides) for: {article['title'][:50]}")
            return slides
        except Exception as e:
            logger.error(f"Carousel generation failed: {e}")
            return self._fallback_carousel(article, num_slides=num_slides or 3)

    def _call_openai_all_platforms(self, article: Dict) -> Dict:
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set — cannot generate platform content")
        title = article.get("title", "")
        content = (article.get("content") or "")[:800]
        source = article.get("source", "Industry News")

        system_prompt = (
            "You are Layla Hassan — a Dubai-based journalist and content strategist who covers UAE business, "
            "lifestyle, and current affairs, with a deep specialism in real estate and property markets. "
            "You write social media posts for Binayah Properties, a respected luxury brokerage in Dubai.\n\n"
            "CRITICAL — adapt your angle to the article topic:\n"
            "- If the article is about real estate, property, construction, or investment: write with market expertise, "
            "connect it to buyers, investors, and the Dubai property landscape.\n"
            "- If the article is about general Dubai or UAE news (economy, infrastructure, government, business, tourism, etc.): "
            "write about it naturally and informatively on its own terms. Do NOT force a real estate angle. "
            "Binayah follows all Dubai/UAE news because their audience lives and invests here — not everything needs to be about property.\n\n"
            "Your writing feels like it comes from a real person who genuinely follows what's happening in the UAE — "
            "not a marketing department. You're informed, occasionally opinionated, and you write differently "
            "on every platform because you actually use them.\n\n"
            "Rules that are non-negotiable:\n"
            "- Never start with 'Exciting news' or 'We are thrilled' or 'Big news'\n"
            "- Never use hollow corporate phrases like 'cutting-edge', 'world-class', 'game-changer'\n"
            "- Never write like a press release\n"
            "- Pull real details from the article — specific numbers, names, locations\n"
            "- Every caption must feel like a real person wrote it after reading the news\n"
            "- Emojis: avoid them entirely. If one genuinely adds meaning, use a maximum of 1 per caption — never to open a sentence or as decoration\n"
            "- Match hashtags to the actual topic — use real estate hashtags only when the article is about real estate"
        )

        user_prompt = f"""Write social media captions for this Dubai/UAE news article.

                            ARTICLE TITLE: {title}
                            ARTICLE CONTENT: {content}
                            SOURCE: {source}

                            Return ONLY a JSON object with this exact structure:

                            {{
                            "instagram": {{
                                "caption": "...",
                                "hashtags": ["tag1", "tag2", ...]
                            }},
                            "facebook": {{
                                "caption": "...",
                                "hashtags": ["tag1", "tag2", ...]
                            }},
                            "twitter": {{
                                "caption": "...",
                                "hashtags": ["tag1", "tag2"]
                            }},
                            "whatsapp": {{
                                "caption": "..."
                            }},
                            "threads": {{
                                "caption": "...",
                                "hashtags": ["tag1", "tag2", "tag3"]
                            }},
                            "linkedin": {{
                                "caption": "...",
                                "hashtags": ["tag1", "tag2", "tag3"]
                            }}
                            }}

                            PLATFORM GUIDELINES:

                            INSTAGRAM (max 2200 chars, 20 hashtags):
                            - Open with a single line that makes someone stop scrolling — a surprising stat, a sharp observation, or a question they actually wonder about
                            - Follow with 2-3 short paragraphs: what happened, why it matters for people living and investing in Dubai/UAE, what to watch next
                            - Use line breaks intentionally — not every sentence on its own line
                            - 1 emoji max; omit entirely if the tone doesn't call for one
                            - End with one line that invites engagement, not "link in bio"
                            - Hashtags: mix of high-reach tags relevant to the topic (#DubaiRealEstate if property, #Dubai #UAE if general news), niche location/topic tags, and always #BinayahProperties

                            FACEBOOK (max 600 chars, 5-8 hashtags):
                            - Community tone — sharing something interesting with people who live, work, and invest in Dubai/UAE
                            - More explanatory than Instagram — give context, not just a hook
                            - End with a genuine question that invites real discussion
                            - Conversational, not corporate

                            TWITTER (max 245 chars for caption, 2-3 hashtags separate):
                            - One sharp take. The most interesting number, name, or implication from the article
                            - Write like someone who has an opinion, not someone summarizing a press release
                            - No filler. Every word earns its place
                            - Do NOT include hashtags in caption field — put them in hashtags array

                            WHATSAPP (max 300 chars, NO hashtags):
                            - Broadcast channel / story tone
                            - Like forwarding something to a group chat of Dubai professionals and residents
                            - Warm, direct, zero jargon, zero hashtags
                            - No hashtags field needed

                            THREADS (max 480 chars, 3-5 hashtags):
                            - Personality and genuine takes — not polished marketing
                            - Could be a question, a slightly contrarian observation, or a real take on what the numbers mean
                            - More casual than Instagram, smarter than Twitter
                            - Light hashtags — not a wall of tags

                            LINKEDIN (max 3000 chars, 3-5 hashtags):
                            - Professional but not stiff — write like a senior broker sharing a thoughtful take with their network
                            - Open with a sharp first line (no "I'm excited to share" or "Big news") — a key stat, a market observation, or a question
                            - Body: 2-3 short paragraphs covering what happened, why it matters for UAE real estate investors and professionals, and what to watch
                            - LinkedIn audiences include investors, developers, HNW buyers, and industry professionals — speak to that mix
                            - End with a genuine insight or a question that invites professional discussion
                            - Use line breaks to aid readability — avoid walls of text
                            - 1 emoji max; omit if the tone doesn't call for them
                            - Hashtags: professional tags like #UAERealEstate #DubaiProperty #PropertyInvestment #BinayahProperties"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.82,
            max_tokens=2200,
            response_format={"type": "json_object"},
        )

        return json.loads(response.choices[0].message.content or "{}")

    def _call_openai_carousel(self, article: Dict, num_slides: Optional[int] = None) -> Dict:
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set — cannot generate carousel angles")
        title = article.get("title", "")
        content = (article.get("content") or "")[:800]

        ai_decides = num_slides is None
        slide_count_instruction = (
            f"Decide how many slides this article deserves based on its richness and depth. "
            f"Choose between 3 and 8 slides. Include a top-level 'suggested_slides' integer in your JSON response "
            f"set to the number of slides you chose."
        ) if ai_decides else (
            f"Create exactly {num_slides} slides — no more, no fewer."
        )
        n = num_slides if num_slides else 5  
        example_slides = "\n".join(
            f'    {{\n      "slide_number": {i},\n      "angle_label": "slide_{i}",\n'
            f'      "headline": "SHORT HEADLINE FOR SLIDE {i} — max 8 words",\n'
            f'      "slide_caption": "Caption for slide {i}."\n    }}'
            for i in range(1, min(n + 1, 5))  
        )
        ellipsis = '    ...' if n > 4 else ''

        system_prompt = (
            "You are Layla Hassan — a Dubai-based journalist and content strategist who covers UAE business, "
            "lifestyle, and real estate, writing carousel content for Binayah Properties on Instagram.\n"
            "A carousel post tells a story across multiple slides. Each slide is a different angle on the same article — not a summary, a perspective.\n"
            "Adapt your angle to the article: if it's real estate news, connect it to the property market; "
            "if it's general Dubai/UAE news, write about it naturally without forcing a real estate angle.\n"
            "Write like a real person. Pull specific details from the article. No corporate language."
        )

        user_prompt = f"""Create carousel slide angles for this article.

                        ARTICLE TITLE: {title}
                        ARTICLE CONTENT: {content}

                        SLIDE COUNT: {slide_count_instruction}

                        SLIDE STRUCTURE GUIDE (adapt as needed for your chosen count):
                        - Slide 1 — Hook: The most compelling stat, name, or fact. 1-2 punchy sentences.
                        - Middle slides — Context & depth: Why it matters, who it affects, specific data points, implications for Dubai/UAE. One distinct angle per slide.
                        - Last slide — Forward look: What this means in the next 6-12 months. End with a line that makes the reader want to save or reach out.

                        Return ONLY a JSON object:

                        {{
                        {"'suggested_slides': <your chosen number>," if ai_decides else ""}
                        "slides": [
                        {example_slides}
                        {ellipsis}
                        ]
                        }}

                        Rules:
                        - Headlines are for image overlays — 5-8 words, punchy, specific to THIS article
                        - Each slide caption should feel like a different paragraph in a smart analysis
                        - Pull real details — specific companies, numbers, locations from the article
                        - Every slide must add new information or a new angle — no repetition"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )

        return json.loads(response.choices[0].message.content or "{}")

    def _parse_and_validate_platforms(self, raw: Dict, article: Dict) -> Dict[str, Dict]:
        result = {}
        fallback = self._fallback_all_platforms(article)

        for platform, spec in PLATFORM_SPECS.items():
            p_data = raw.get(platform, {})
            caption = (p_data.get("caption") or "").strip()
            hashtags = p_data.get("hashtags") or []

            if not caption:
                caption = fallback[platform]["caption"]
                hashtags = fallback[platform].get("hashtags", [])

            max_chars = spec["max_caption_chars"]
            if len(caption) > max_chars:
                caption = caption[:max_chars - 1].rsplit(" ", 1)[0] + "\u2026"

            clean_tags = []
            for tag in hashtags:
                tag = re.sub(r"[^a-zA-Z0-9_]", "", tag.replace("#", "").strip())
                if tag and len(tag) > 1:
                    clean_tags.append(tag)

            max_tags = spec["max_hashtags"]
            result[platform] = {
                "caption": caption,
                "hashtags": clean_tags[:max_tags] if spec["has_hashtags"] else [],
            }

        return result

    def _parse_carousel(self, raw: Dict, article: Dict, num_slides: Optional[int] = None) -> List[Dict]:
        slides_raw = raw.get("slides", [])
        min_required = num_slides if num_slides else 1
        if not slides_raw or len(slides_raw) < min_required:
            return self._fallback_carousel(article, num_slides=num_slides or 3)

        # If AI chose the count, take all it returned; if caller fixed it, enforce exactly that
        limit = num_slides if num_slides else len(slides_raw)
        slides = []
        for s in slides_raw[:limit]:
            slides.append({
                "slide_number": int(s.get("slide_number", len(slides) + 1)),
                "angle_label": s.get("angle_label", ""),
                "headline": (s.get("headline") or "")[:80].strip(),
                "slide_caption": (s.get("slide_caption") or "")[:400].strip(),
            })
        return slides

    def _fallback_all_platforms(self, article: Dict) -> Dict[str, Dict]:
        title = article.get("title", "Dubai Real Estate Update")
        source = article.get("source", "Industry News")

        return {
            "instagram": {
                "caption": f"Something worth knowing about Dubai's property market.\n\n{title}\n\nVia {source}. Follow for daily market updates.",
                "hashtags": ["DubaiRealEstate", "BinayahProperties", "DubaiProperty", "UAEProperty", "LuxuryRealEstate", "PropertyInvestment", "Dubai", "RealEstateNews"],
            },
            "facebook": {
                "caption": f"{title}\n\nVia {source}. What's your take on this?",
                "hashtags": ["DubaiRealEstate", "BinayahProperties", "DubaiProperty", "UAEProperty"],
            },
            "twitter": {
                "caption": title[:200],
                "hashtags": ["DubaiRealEstate", "Dubai"],
            },
            "whatsapp": {
                "caption": f"Worth a read \u2014 {title}. Via {source}.",
                "hashtags": [],
            },
            "threads": {
                "caption": f"{title}\n\nVia {source} \u2014 thoughts?",
                "hashtags": ["DubaiRealEstate", "Dubai", "PropertyMarket"],
            },
            "linkedin": {
                "caption": (
                    f"{title}\n\n"
                    f"Via {source} \u2014 an update worth tracking for anyone following the UAE property market.\n\n"
                    "At Binayah Properties, we stay on top of developments like this to keep our clients informed and ahead of the curve. "
                    "What's your read on this?"
                ),
                "hashtags": ["UAERealEstate", "DubaiProperty", "PropertyInvestment", "BinayahProperties", "Dubai"],
            },
        }

    def _fallback_carousel(self, article: Dict, num_slides: int = 3) -> List[Dict]:
        title = article.get("title", "Dubai Real Estate Update")
        base = [
            {"slide_number": 1, "angle_label": "hook",        "headline": title[:60].upper(),   "slide_caption": title},
            {"slide_number": 2, "angle_label": "context",     "headline": "WHAT THIS MEANS",    "slide_caption": "This development reflects continued momentum in Dubai's property market."},
            {"slide_number": 3, "angle_label": "opportunity", "headline": "THE OPPORTUNITY",    "slide_caption": "Contact Binayah Properties to understand how this affects your investment strategy."},
            {"slide_number": 4, "angle_label": "detail",      "headline": "KEY DETAILS",        "slide_caption": "Follow Binayah Properties for in-depth analysis of the UAE real estate market."},
            {"slide_number": 5, "angle_label": "insight",     "headline": "MARKET INSIGHT",     "slide_caption": "Dubai's property market continues to attract global investors seeking strong returns."},
            {"slide_number": 6, "angle_label": "data",        "headline": "BY THE NUMBERS",     "slide_caption": "Strong fundamentals underpin continued growth across key Dubai communities."},
            {"slide_number": 7, "angle_label": "outlook",     "headline": "LOOKING AHEAD",      "slide_caption": "Demand for quality UAE real estate remains robust heading into 2025 and beyond."},
            {"slide_number": 8, "angle_label": "cta",         "headline": "GET IN TOUCH",       "slide_caption": "Speak to a Binayah Properties advisor to explore your next investment opportunity."},
        ]
        return base[:num_slides]


class RealEstatePostCreator:
    """
    Create complete social media posts with professional captions,
    optimized hashtags, and platform-specific formatting.
    """
    
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-4o-mini"
        self.brand_voice = {
            "name": "Binayah Properties",
            "tone": "Professional, sophisticated, trustworthy",
            "audience": "High-net-worth individuals, luxury property investors",
            "style": "Informative yet aspirational, data-driven but elegant"
        }
    
    def create_post(
        self,
        article: Dict,
        platform: Literal["instagram", "twitter", "both"] = "instagram",
        include_stats: bool = True,
        include_cta: bool = True
    ) -> Dict:
        try:
            logger.info(f"\nCreating post for: {article['title'][:50]}...")
            caption = self._generate_caption(article, platform, include_stats, include_cta)
            hashtags = self._generate_hashtags(article, platform)
            formatted_post = self._format_for_platform(caption, hashtags, platform)
            post_data = {
                "caption": formatted_post["caption"],
                "hashtags": hashtags,
                "full_text": formatted_post["full_text"],
                "platform": platform,
                "character_count": len(formatted_post["full_text"]),
                "word_count": len(formatted_post["full_text"].split()),
                "hashtag_count": len(hashtags),
                "source_article": {
                    "title": article["title"],
                    "url": article.get("url", ""),
                    "source": article.get("source", "")
                }
            }
            logger.info(f"   Post created ({post_data['character_count']} chars)")
            return post_data
        except Exception as e:
            logger.error(f"   Post creation failed: {e}")
            return self._create_fallback_post(article, platform)
    
    def _generate_caption(self, article: Dict, platform: str, include_stats: bool, include_cta: bool) -> str:
        max_length = {"instagram": 2200, "twitter": 280, "both": 280}[platform]
        prompt = f"""Write a professional social media caption for Binayah Properties, a luxury real estate company in Dubai.

                    Article Title: {article['title']}
                    Article Summary: {article.get('content', '')[:500]}
                    Source: {article.get('source', 'Industry News')}

                    Requirements:
                    - Platform: {platform.upper()}
                    - Max length: {max_length} characters
                    - Tone: {self.brand_voice['tone']}
                    - Audience: {self.brand_voice['audience']}
                    - Hook on first line
                    {"- Include key statistics" if include_stats else ""}
                    {"- Include subtle call-to-action" if include_cta else ""}
                    - 0-1 emojis only; prefer none

                    Write ONLY the caption text, no hashtags."""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a professional copywriter for luxury real estate social media."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=500
            )
            caption = response.choices[0].message.content.strip()
            if len(caption) > max_length:
                caption = caption[:max_length - 3] + "..."
            return caption
        except Exception as e:
            logger.error(f"Caption generation failed: {e}")
            return self._create_fallback_caption(article)
    
    def _generate_hashtags(self, article: Dict, platform: str) -> List[str]:
        target_count = {"instagram": 20, "twitter": 3, "both": 5}[platform]
        prompt = f"""Generate {target_count} optimized hashtags for this Dubai real estate post.
                    Article: {article['title']}
                    Content: {article.get('content', '')[:300]}
                    Mix of branded, location, property type, and audience hashtags. No spaces, PascalCase.
                    Return ONLY a JSON array: ["DubaiRealEstate", "LuxuryProperty", ...]"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a social media strategist specializing in luxury real estate."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=200,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content or "{}")
            hashtags = result.get("hashtags", [])
            if not hashtags and result:
                hashtags = list(result.values())[0] if result else []
            clean_hashtags = []
            for tag in hashtags:
                tag = tag.replace('#', '').replace(' ', '').strip()
                if tag and len(tag) > 2:
                    clean_hashtags.append(tag)
            return clean_hashtags[:target_count]
        except Exception as e:
            logger.error(f"Hashtag generation failed: {e}")
            return self._get_default_hashtags()[:target_count]
    
    def _format_for_platform(self, caption: str, hashtags: List[str], platform: str) -> Dict:
        if platform == "instagram":
            full_text = f"{caption}\n\n\u2022\n\u2022\n{' '.join(['#' + tag for tag in hashtags])}"
        elif platform == "twitter":
            hashtag_text = ' '.join(['#' + tag for tag in hashtags[:3]])
            available_chars = 280 - len(hashtag_text) - 2
            if len(caption) > available_chars:
                caption = caption[:available_chars - 3] + "..."
            full_text = f"{caption} {hashtag_text}"
        else:
            full_text = f"{caption}\n\n{' '.join(['#' + tag for tag in hashtags])}"
        return {"caption": caption, "full_text": full_text}
    
    def _create_fallback_caption(self, article: Dict) -> str:
        return f"{article['title']}\n\nLatest insights from {article.get('source', 'Industry Report')} on Dubai's dynamic real estate market.\n\nStay informed with Binayah Properties."
    
    def _create_fallback_post(self, article: Dict, platform: str) -> Dict:
        caption = self._create_fallback_caption(article)
        hashtags = self._get_default_hashtags()
        return {
            "caption": caption,
            "hashtags": hashtags,
            "full_text": f"{caption}\n\n{' '.join(['#' + tag for tag in hashtags])}",
            "platform": platform,
            "character_count": len(caption),
            "word_count": len(caption.split()),
            "hashtag_count": len(hashtags),
            "source_article": {"title": article["title"], "url": article.get("url", ""), "source": article.get("source", "")}
        }
    
    def _get_default_hashtags(self) -> List[str]:
        return [
            "DubaiRealEstate", "BinayahProperties", "LuxuryProperty", "DubaiProperty",
            "RealEstateInvestment", "Dubai", "UAEProperty", "PropertyInvestor",
            "DubaiLuxury", "RealEstateNews"
        ]



class CompletePostPipeline:
    """Complete pipeline: Article -> Image + Caption -> Final Post"""
    
    def __init__(self):
        from app.services.newsgen.image_generator import RealEstateImageGenerator
        self.image_generator = RealEstateImageGenerator()
        self.post_creator = RealEstatePostCreator()
    
    def create_complete_posts(self, top_articles: List[Dict], num_posts: int = 3, platform: str = "instagram") -> List[Dict]:
        logger.info(f"Creating {num_posts} complete posts")
        complete_posts = []
        
        for i, article in enumerate(top_articles[:num_posts], 1):
            logger.info(f"[{i}/{num_posts}] {article['title'][:60]}...")
            try:
                image_data = self.image_generator.generate_post_image(
                    article, style="luxury", platform=f"{platform}_square", add_branding=True
                )
                post_data = self.post_creator.create_post(article, platform=platform, include_stats=True, include_cta=True)
                complete_post = {
                    **post_data,
                    "image": image_data,
                    "article_data": {
                        "title": article["title"],
                        "url": article.get("url", ""),
                        "source": article["source"],
                        "published_date": article.get("published_date", ""),
                        "confidence_score": article.get("confidence_score", 0),
                        "llm_score": article.get("llm_score", 0),
                        "llm_rank": article.get("llm_rank", i)
                    },
                    "status": "pending_review",
                    "created_at": article.get("fetched_at", "")
                }
                complete_posts.append(complete_post)
                logger.info(f"   Post {i} created successfully")
            except Exception as e:
                logger.error(f"   Failed to create post {i}: {e}")
                continue
        
        logger.info(f"Created {len(complete_posts)} posts")
        return complete_posts
