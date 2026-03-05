"""
AI IMAGE GENERATOR FOR DUBAI REAL ESTATE POSTS
Uses NanoBanana AI to generate professional real estate imagery
Optimized for social media (Instagram/X)

NOTE:
- This file previously used Stability AI.
- Per request, Stability calls are replaced with NanoBanana provider
  (nanobanana.py), without changing other working functions / call-sites.
"""
from __future__ import annotations

import os
import logging
import base64
import io
import json
from typing import Dict, List, Optional, Literal, Tuple
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont  # noqa: F401 (ImageFont kept as in original)

from openai import OpenAI  # noqa: F401 (used dynamically in __init__)

from app.services.image_provider.nanobanana import (
    get_nanobanana_provider_from_env,
    NanoBananaError,
)

logger = logging.getLogger(__name__)


def get_image_provider():
    """
    Return the configured image provider based on IMAGE_PROVIDER env var.
    - "nanobanana" (default, cheap, good for test)
    - "stability" (higher quality, for production)
    Both share the same interface: .generate() and .transform_image()
    """
    provider_name = os.getenv("IMAGE_PROVIDER", "nanobanana").strip().lower()

    if provider_name == "stability":
        from app.services.image_provider.stability import get_stability_provider_from_env
        logger.info("Using Stability AI image provider (production quality)")
        return get_stability_provider_from_env()
    else:
        logger.info("Using NanoBanana image provider (cost-effective)")
        return get_nanobanana_provider_from_env()


class RealEstateImageGenerator:
    """
    Generate professional real estate images using NanoBanana AI
    Specialized for Dubai luxury properties with brand elements
    """

    def __init__(self, *args, **kwargs):
        _ = os.getenv("STABILITY_API_KEY", "").strip()
        self.base_url = (os.getenv("STABILITY_BASE_URL", "") or "").strip()
        if not self.base_url:
            self.base_url = "https://api.stability.ai"

        self._openai_client = None
        try:
            key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
            if key:
                self._openai_client = OpenAI(api_key=key)
        except Exception:
            self._openai_client = None

        self.engine_id = "stable-diffusion-xl-1024-v1-0"

        # Image dimensions
        self.dimensions = {
            "instagram_square": {"width": 1024, "height": 1024},
            "instagram_portrait": {"width": 1080, "height": 1350},
            "instagram_story": {"width": 1080, "height": 1920},  # 9:16
            "carousel_slide": {"width": 1080, "height": 1350},  # 4:5 per slide
            "twitter": {"width": 1200, "height": 675},
            "default": {"width": 1024, "height": 1024},
        }

        # Brand colors (Binayah Properties)
        self.brand_colors = {
            "primary": "#004e41",
            "secondary": "#d1ae4a",
            "white": "#ffffff",
        }

    def generate_post_image(
        self,
        article: Dict,
        style: Literal["modern", "luxury", "minimal", "editorial"] = "luxury",
        platform: Literal["instagram_square", "instagram_portrait", "twitter"] = "instagram_square",
        add_branding: bool = True,
    ) -> Optional[Dict]:
        """
        Generate a professional image for a real estate post
        """
        try:
            logger.info(f"\n🎨 GENERATING IMAGE FOR: {article['title'][:50]}...")

            # 1. Generate prompt from article
            prompt = self._create_image_prompt(article, style)
            positive_prompt = prompt[0] if isinstance(prompt, tuple) else prompt
            logger.info(f"   Prompt: {positive_prompt[:100]}...")

            # 2. Generate base image (NOW via NanoBanana, through the same method name)
            base_image = self._generate_with_stability(prompt, platform)

            if not base_image:
                logger.warning("   ⚠️ First generation attempt failed — retrying with direct article prompt")
                title_for_retry = article.get("title", "")
                raw_for_retry = self._extract_article_text(article, max_chars=600)
                first_para_retry = self._extract_first_paragraph(raw_for_retry, max_chars=250)
                direct_positive = (
                    f"Ultra realistic professional news photography, "
                    f"visual scene accurately representing this news story — "
                    f"title: \"{title_for_retry[:120]}\", "
                    + (f"context: {first_para_retry}, " if first_para_retry else "")
                    + "photorealistic, natural lighting, cinematic composition, "
                    "leave clean empty lower-middle area for headline overlay, "
                    "no text, no logos, no watermark, 4:5 aspect ratio"
                )
                direct_negative = (
                    "text, watermark, logo, blurry, low quality, distortion, "
                    "cgi, illustration, cartoon, oversaturated, advertisement"
                )
                logger.info(f"   🔄 Retry prompt: {direct_positive[:120]}...")
                base_image = self._generate_with_stability(
                    (direct_positive, direct_negative), platform
                )
                if not base_image:
                    logger.error("   ❌ Retry also failed — returning None")
                    return {"prompt": positive_prompt, "image_bytes": None}
                positive_prompt = direct_positive
                logger.info("   ✅ Retry succeeded with direct article prompt")

            # 3. Add branding elements
            if add_branding:
                final_image = self._add_branding_overlay(base_image, article)
            else:
                final_image = base_image

            # 4. Save and return
            image_data = self._prepare_image_data(final_image, article)
            image_data["prompt"] = positive_prompt

            logger.info("   ✅ Image generated successfully")
            return image_data

        except Exception as e:
            logger.error(f"   ❌ Image generation error: {e}")
            return None

    def transform_article_image(
        self,
        article_image_url: str,
        article: Dict,
        style: Literal["modern", "luxury", "minimal", "editorial"] = "luxury",
        transformation_strength: float = 0.5,
        platform: Literal["instagram_square", "instagram_portrait", "twitter"] = "instagram_square",
    ) -> Optional[Dict]:
        """
        Transform an article's original image using NanoBanana image-to-image.
        """
        try:
            logger.info(f"\n🎨 TRANSFORMING ARTICLE IMAGE: {article['title'][:50]}...")
            logger.info(f"   Source image: {article_image_url}")

            # 1. Download article image
            article_image_bytes = self._download_image(article_image_url)
            if not article_image_bytes:
                logger.warning("   ⚠️ Article image not available (will use AI generation instead)")
                return None

            # 2. Resize to target dimensions
            resized_bytes = self._resize_image_bytes(article_image_bytes, platform)

            # 3. Create transformation prompt
            transform_prompt = self._create_transformation_prompt(article, style)
            logger.info(f"   Transform prompt: {transform_prompt[:100]}...")

            # 4. Transform
            transformed_image = self._transform_with_stability(
                resized_bytes,
                transform_prompt,
                strength=transformation_strength,
            )

            if not transformed_image:
                logger.warning("   ⚠️ Image transformation failed (will use AI generation instead)")
                return None

            # 5. Prepare result
            image_data = self._prepare_image_data(transformed_image, article)
            image_data["prompt"] = transform_prompt
            image_data["transformation_source"] = article_image_url
            image_data["transformation_strength"] = transformation_strength

            logger.info("   ✅ Article image transformed successfully")
            return image_data

        except Exception as e:
            logger.error(f"   ❌ Image transformation error: {e}")
            return None

    def generate_story_image(
        self,
        article: Dict,
        style: str = "luxury",
        platform: str = "instagram_story",
    ) -> Optional[Dict]:
        """
        Generate a 9:16 vertical story background image.
        """
        try:
            logger.info(f"Generating story image: {article['title'][:50]}...")
            prompt = self._create_image_prompt(article, style)
            base_image = self._generate_with_stability(prompt, platform)
            if not base_image:
                return {"prompt": prompt[0] if isinstance(prompt, tuple) else prompt, "image_bytes": None}
            image_data = self._prepare_image_data(base_image, article)
            image_data["prompt"] = prompt[0] if isinstance(prompt, tuple) else prompt
            image_data["format_type"] = "story"
            logger.info("Story image generated successfully")
            return image_data
        except Exception as e:
            logger.error(f"Story image generation error: {e}")
            return None

    def generate_carousel_slides(
        self,
        article: Dict,
        angles: List[Dict],
        style: str = "luxury",
    ) -> List[Dict]:
        """
        Generate background images for carousel slides, one per angle.
        """
        results = []

        for angle in angles:
            slide_num = angle.get("slide_number", len(results) + 1)
            headline = angle.get("headline", "")
            slide_caption = angle.get("slide_caption", "")
            angle_label = angle.get("angle_label", "")

            logger.info(
                f"  Generating carousel slide {slide_num}/{len(angles)} ({angle_label}): {headline[:40]}..."
            )

            try:
                slide_article = {
                    **article,
                    "title": headline or article.get("title", ""),
                    "content": f"{slide_caption}\n\n{article.get('content', '')[:400]}",
                }
                prompt = self._create_carousel_slide_prompt(slide_article, style, angle_label)
                base_image = self._generate_with_stability(prompt, "carousel_slide")

                if base_image:
                    image_data = self._prepare_image_data(base_image, slide_article)
                    image_data["prompt"] = prompt[0] if isinstance(prompt, tuple) else prompt
                else:
                    image_data = {"image_bytes": None, "prompt": ""}

                results.append(
                    {
                        "slide_number": slide_num,
                        "headline": headline,
                        "slide_caption": slide_caption,
                        "angle_label": angle_label,
                        **image_data,
                        "format_type": "carousel",
                    }
                )
                logger.info(f"  Slide {slide_num} generated")

            except Exception as e:
                logger.error(f"  Slide {slide_num} generation failed: {e}")
                results.append(
                    {
                        "slide_number": slide_num,
                        "headline": headline,
                        "slide_caption": slide_caption,
                        "angle_label": angle_label,
                        "image_bytes": None,
                        "format_type": "carousel",
                    }
                )

        return results

    def _create_carousel_slide_prompt(
        self,
        article: Dict,
        style: str,
        angle_label: str,
    ) -> tuple:
        """
        Create a prompt specific to a carousel slide angle.
        """
        angle_style_map = {
            "hook": "dramatic editorial, high contrast, eye-catching, immediate impact",
            "context": "informative documentary, clean composition, credible journalistic style",
            "opportunity": "aspirational luxury, warm lighting, optimistic, forward-looking",
        }
        angle_hint = angle_style_map.get(angle_label, "")

        title = article.get("title", "")
        content = (article.get("content") or "")[:400]

        if self._openai_client:
            try:
                style_modifiers = {
                    "luxury": "premium business-news look, polished, high-end",
                    "modern": "clean editorial, minimal, sleek, corporate",
                    "minimal": "minimal, lots of negative space, soft gradients",
                    "editorial": "Reuters/Bloomberg editorial visual, documentary, serious",
                }
                resp = self._openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You generate background image prompts for Instagram carousel slide visuals. "
                                "No text, no logos. Leave space for text overlay in lower section."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Carousel slide angle: {angle_label}. Visual direction: {angle_hint}.\n"
                                f"Article: {title}\nContent: {content}\n"
                                f"Style: {style_modifiers.get(style, '')}\n\n"
                                f"Return JSON: {{\"positive_prompt\": \"...\", \"negative_prompt\": \"...\"}}"
                            ),
                        },
                    ],
                    temperature=0.72,
                    max_tokens=300,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                pos = (data.get("positive_prompt") or "").strip()
                neg = (data.get("negative_prompt") or "").strip()
                if pos and neg:
                    return pos, neg
            except Exception as e:
                logger.warning(f"OpenAI carousel prompt failed: {e}")

        return self._fallback_image_prompt_news(style, title, content)

    # AVIF magic bytes
    _AVIF_BRANDS = (b"avif", b"avis", b"heic", b"heif", b"mif1", b"msf1")

    # Content-types that Stability AI and Pillow struggle with → force convert
    _FORCE_CONVERT_CONTENT_TYPES = {
        "image/avif",
        "image/heic",
        "image/heif",
        "image/webp",
        "image/tiff",
        "image/bmp",
        "image/x-bmp",
    }

    _DOWNLOAD_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Cache-Control": "no-cache",
    }

    def _detect_avif_by_magic(self, data: bytes) -> bool:
        if len(data) < 12:
            return False
        if data[4:8] == b"ftyp":
            brand = data[8:12]
            return brand in self._AVIF_BRANDS
        return False

    def _download_image(self, image_url: str) -> Optional[bytes]:
        """
        Download image from URL with retry, CDN-friendly headers, content-type
        validation and immediate conversion of unsupported formats to JPEG.
        Returns bytes ready for further processing, or None on failure.

        Enhancement: If the provided URL is an ARTICLE (HTML), extract og:image/twitter:image
        and download that image instead.
        """
        import re
        import random
        import time
        from urllib.parse import urlparse, urlunparse, urljoin

        import requests
        from bs4 import BeautifulSoup

        def _sanitize_url(u: str) -> str:
            if u.startswith("//"):
                u = "https:" + u
            parsed = urlparse(u)
            path = parsed.path
            while "//" in path:
                path = path.replace("//", "/")
            return urlunparse(parsed._replace(path=path))

        def _is_html_response(resp: requests.Response, raw: bytes) -> bool:
            ct = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
            if "text/html" in ct:
                return True
            head = raw[:512].lstrip().lower()
            return head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<head" in head

        def _extract_best_image_from_html(html: str, base_url: str) -> Optional[str]:
            soup = BeautifulSoup(html, "html.parser")

            for sel in [
                ('meta[property="og:image"]', "content"),
                ('meta[name="twitter:image"]', "content"),
                ('meta[property="og:image:url"]', "content"),
            ]:
                tag = soup.select_one(sel[0])
                if tag and tag.get(sel[1]):
                    return urljoin(base_url, tag.get(sel[1]).strip())

            best = None
            best_score = -1
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if not src:
                    continue
                src = urljoin(base_url, src.strip())
                w = img.get("width")
                h = img.get("height")
                try:
                    w = int(w) if w else 0
                    h = int(h) if h else 0
                except Exception:
                    w, h = 0, 0
                score = (w * h) if (w and h) else 0
                if score > best_score:
                    best_score = score
                    best = src

            return best

        image_url = _sanitize_url(image_url)

        session = requests.Session()

        headers = dict(self._DOWNLOAD_HEADERS)
        headers.setdefault("Accept", "image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
        headers.setdefault("Accept-Encoding", "gzip, deflate")
        try:
            p = urlparse(image_url)
            if p.scheme and p.netloc:
                headers.setdefault("Referer", f"{p.scheme}://{p.netloc}/")
        except Exception:
            pass

        max_attempts = 3
        MAX_BYTES = 12 * 1024 * 1024
        last_error = None

        def _download_bytes(url: str, allow_html: bool = False) -> Optional[bytes]:
            nonlocal last_error
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = session.get(url, headers=headers, timeout=(10, 30), allow_redirects=True, stream=True)
                    status = resp.status_code

                    chunks = []
                    size = 0
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_BYTES:
                            last_error = f"too large > {MAX_BYTES}"
                            return None

                    raw = b"".join(chunks)
                    if not raw:
                        last_error = "empty response"
                        continue

                    if status >= 400:
                        last_error = f"HTTP {status}"
                        if status < 500:
                            return None
                        continue

                    if (not allow_html) and _is_html_response(resp, raw):
                        return None

                    content_type = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()

                    needs_conversion = (
                        content_type in self._FORCE_CONVERT_CONTENT_TYPES
                        or self._detect_avif_by_magic(raw)
                    )
                    if needs_conversion:
                        converted = self._to_jpeg_bytes(raw)
                        return converted if converted else raw

                    return raw

                except requests.exceptions.Timeout:
                    last_error = "timeout"
                except requests.exceptions.ConnectionError as exc:
                    last_error = str(exc)
                except Exception as exc:
                    last_error = str(exc)

                time.sleep((0.4 * (2 ** (attempt - 1))) + random.random() * 0.2)

            return None

        # 1) direct image
        raw = _download_bytes(image_url)
        if raw:
            return raw

        # 2) treat as HTML article
        try:
            html_raw = _download_bytes(image_url, allow_html=True)
            if not html_raw:
                return None

            html_text = html_raw.decode("utf-8", errors="ignore")
            best_img = _extract_best_image_from_html(html_text, image_url)
            if not best_img:
                return None
            return _download_bytes(best_img)
        except Exception:
            return None

    def _to_jpeg_bytes(self, image_bytes: bytes) -> Optional[bytes]:
        """
        Convert any image format to JPEG bytes.
        """
        try:
            import pillow_avif  # noqa: F401
        except ImportError:
            pass

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90, optimize=True)
            return out.getvalue()
        except Exception as pil_err:
            logger.debug(f"   Standard Pillow open failed: {pil_err}")

        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90, optimize=True)
            logger.info("   ✅ HEIF decoded via pillow-heif")
            return out.getvalue()
        except ImportError:
            logger.debug("   pillow-heif not installed")
        except Exception as heif_err:
            logger.debug(f"   pillow-heif open failed: {heif_err}")

        try:
            import imageio  # type: ignore

            arr = imageio.imread(io.BytesIO(image_bytes))
            img = Image.fromarray(arr).convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90, optimize=True)
            logger.info("   ✅ Image decoded via imageio")
            return out.getvalue()
        except ImportError:
            logger.debug("   imageio not installed")
        except Exception as iio_err:
            logger.debug(f"   imageio open failed: {iio_err}")

        logger.warning("   ⚠️ All conversion attempts failed for this image format")
        return None

    def _resize_image_bytes(self, image_bytes: bytes, platform: str) -> bytes:
        """
        Resize image to target platform dimensions.
        Converts unsupported formats to JPEG first.
        """
        working_bytes = image_bytes
        try:
            Image.open(io.BytesIO(working_bytes)).convert("RGB")
        except Exception:
            logger.warning("   ⚠️ Cannot open image directly, attempting format conversion")
            converted = self._to_jpeg_bytes(working_bytes)
            if converted:
                working_bytes = converted
            else:
                logger.error("   ❌ Format conversion failed in resize, returning original bytes")
                return image_bytes

        try:
            img = Image.open(io.BytesIO(working_bytes)).convert("RGB")
            dims = self.dimensions.get(platform, self.dimensions["default"])
            img = self._resize_and_crop(img, dims["width"], dims["height"])
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90, optimize=True)
            return out.getvalue()
        except Exception as e:
            logger.error(f"   ⚠️ Resize failed: {e}")
            fallback = self._to_jpeg_bytes(working_bytes)
            return fallback if fallback else image_bytes

    def _resize_and_crop(self, img: Image.Image, target_width: int, target_height: int) -> Image.Image:
        img_ratio = img.width / img.height
        target_ratio = target_width / target_height

        if img_ratio > target_ratio:
            new_height = target_height
            new_width = int(target_height * img_ratio)
        else:
            new_width = target_width
            new_height = int(target_width / img_ratio)

        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        left = (new_width - target_width) // 2
        top = (new_height - target_height) // 2
        right = left + target_width
        bottom = top + target_height

        return img.crop((left, top, right, bottom))

    @staticmethod
    def _extract_article_text(article: Dict, max_chars: int = 800) -> str:
        """
        Robustly extract body text from an article dict.
        Tries content → body → description → summary in order.
        Returns first non-empty value, truncated to max_chars.
        """
        for field in ("content", "body", "description", "summary"):
            val = (article.get(field) or "").strip()
            if val:
                return val[:max_chars]
        return ""

    @staticmethod
    def _extract_first_paragraph(text: str, max_chars: int = 300) -> str:
        """Return first non-empty paragraph from text, capped at max_chars."""
        if not text:
            return ""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if paragraphs:
            return paragraphs[0][:max_chars]
        return text[:max_chars]


    def _create_transformation_prompt(self, article: Dict, style: str) -> str:
        title = (article.get("title") or "").strip()

        raw_content = self._extract_article_text(article, max_chars=800)
        first_para = self._extract_first_paragraph(raw_content, max_chars=250)

        if title and first_para:
            topic_hint = f" for a story about: {title[:80]}. Key context: {first_para}"
        elif title:
            topic_hint = f" for a story about: {title[:80]}"
        else:
            topic_hint = ""

        style_transformations = {
            "modern": f"Enhance this photo{topic_hint}, sharpen details, improve color balance, professional lighting correction, keep the exact same composition and subject",
            "luxury": f"Enhance this photo{topic_hint}, improve lighting and clarity, subtle warm tones, professional color grading, keep the exact same composition and subject",
            "minimal": f"Enhance this photo{topic_hint}, clean up noise, improve contrast, soft natural tones, keep the exact same composition and subject",
            "editorial": f"Enhance this photo{topic_hint}, sharpen details, professional news-quality clarity, neutral color correction, keep the exact same composition and subject",
        }

        base_transform = style_transformations.get(style, style_transformations["luxury"])

        prompt = (
            f"{base_transform}, "
            " READ the article carefully and identify the ACTUAL topic (it could be real estate, transport, technology, government policy, infrastructure, finance, tourism, etc.)"
            "Create a visual that accurately represents THAT topic, not generic real estate"
            "If the article IS about real estate, include relevant property/architecture visuals"
            "If the article is about something else (e.g. air taxis, metro expansion, new regulations), depict THAT subject"
            "Include relevant location elements if mentioned (Dubai, Abu Dhabi, etc.)"
            "Apply the requested style (editorial, luxury, etc.)"
            "Must include: no text, no logos, no watermark"
            "Include composition instruction: leave clear empty area in lower-middle for headline overlay"
            "Make it photorealistic and credible as a news visual"
        )
        return prompt

    def _transform_with_stability(
        self,
        image_bytes: bytes,
        prompt: str,
        strength: float = 0.5,
    ) -> Optional[bytes]:
        """
        Transform image using NanoBanana image-to-image.
        (Method name kept for compatibility.)
        """
        try:
            provider = get_image_provider()

            negative_prompt = (
                "text, watermark, logo, letters, blurry, low quality, distortion, "
                "over-processed, oversaturated, fake, unnatural, "
                "advertisement, sale banner, phone number, staged interior"
            )

            result = provider.transform_image(
                image_bytes=image_bytes,
                prompt=prompt,
                negative_prompt=negative_prompt,
                strength=strength,
            )

            return result.bytes

        except Exception as e:
            logger.error(f"   ❌ Image transformation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"   ❌ Unexpected error during transformation: {e}")
            return None

    def _create_image_prompt(self, article: Dict, style: str) -> Tuple[str, str]:
        """
        Create an AI image prompt based on article content.
        Returns: (positive_prompt, negative_prompt)
        """
        title = article.get("title", "")
        content = self._extract_article_text(article, max_chars=800)

        style_modifiers = {
            "modern": "clean editorial news graphic style, minimal, sleek, corporate",
            "luxury": "premium business-news look, polished, high-end, subtle gold accents feel (no actual text)",
            "minimal": "very minimal composition, lots of negative space, soft gradients, subtle skyline silhouette",
            "editorial": "Reuters/Bloomberg-like editorial visual tone, documentary, serious, credible",
        }

        system_prompt = (
            "You are an expert visual director for social media NEWS posts.\n"
            "Your job is to generate image-generation prompts for background visuals used in square posts.\n"
            "These visuals will have text added later by design templates, so you must leave clean space.\n"
            "You must accurately represent the article's actual topic in the visual.\n"
            "Do NOT force real estate imagery when the article is about something else.\n"
            "Do NOT create sales advertisements or property listing photos."
        )

        user_prompt = f"""
                        Generate ONE background image prompt that MATCHES this article's topic.

                        ARTICLE:
                        TITLE: {title}
                        CONTENT: {content}

                        STYLE: {style}
                        STYLE_HINT: {style_modifiers.get(style, style_modifiers["editorial"])}

                        Return JSON ONLY:
                        {{
                        "positive_prompt": "50-110 words",
                        "negative_prompt": "comma-separated negatives"
                        }}

                        REQUIREMENTS for positive_prompt:
                        - READ the article carefully and identify the ACTUAL topic (it could be real estate, transport, technology, government policy, infrastructure, finance, tourism, etc.)
                        - Create a visual that accurately represents THAT topic, not generic real estate
                        - If the article IS about real estate, include relevant property/architecture visuals
                        - If the article is about something else (e.g. air taxis, metro expansion, new regulations), depict THAT subject
                        - Include relevant location elements if mentioned (Dubai, Abu Dhabi, etc.)
                        - Apply the requested style (editorial, luxury, etc.)
                        - Must include: "no text, no logos, no watermark, 4:5 aspect ratio"
                        - Include composition instruction: leave clear empty area in lower-middle for headline overlay
                        - Make it photorealistic and credible as a news visual

                        REQUIREMENTS for negative_prompt:
                        - Must include: text, watermark, logo, blurry, low quality, distortion
                        - Also avoid: advertisement, sale banner, phone number, cartoon, illustration
                        """

        try:
            if not self._openai_client:
                raise RuntimeError("OpenAI client not configured")

            resp = self._openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=500,
                response_format={"type": "json_object"},
            )

            data = json.loads(resp.choices[0].message.content or "{}")
            positive = (data.get("positive_prompt") or "").strip()
            negative = (data.get("negative_prompt") or "").strip()

            if len(positive) < 20 or len(negative) < 10:
                logger.warning("OpenAI returned incomplete prompts, using fallback")
                return self._fallback_image_prompt_news(style, title, content)

            return positive, negative

        except Exception as e:
            logger.warning(f"OpenAI prompt generation failed: {e}, using fallback")
            return self._fallback_image_prompt_news(style, title, content)

    def _fallback_image_prompt_news(self, style: str, title: str, content: str = "") -> Tuple[str, str]:
        """
        Build a prompt driven entirely by the article title and first paragraph.
        No generic boilerplate — the news content IS the prompt subject.
        """
        style_mood = {
            "modern":    "clean corporate editorial photography, sharp and sleek",
            "luxury":    "premium editorial photography, warm cinematic lighting, high-end",
            "minimal":   "minimal editorial photography, soft natural light, breathing space",
            "editorial": "documentary news photography, neutral tones, credible and realistic",
        }.get(style, "documentary news photography, neutral tones, credible and realistic")

        first_para = self._extract_first_paragraph(content, max_chars=300)

        if not title and not first_para:
            title = "Dubai real estate market"

        if title and first_para:
            subject = (
                f"{title.strip()}. "
                f"{first_para.strip()}"
            )
        else:
            subject = title.strip() or first_para.strip()

        positive = (
            f"Photorealistic editorial photograph depicting: {subject} — "
            f"{style_mood}, "
            "high dynamic range, sharp focus, 35mm lens, cinematic natural lighting, "
            "realistic shadows, credible news visual, "
            "large clean empty space in lower portion reserved for text overlay, "
            "no text, no logos, no watermark, 4:5 aspect ratio"
        )

        negative = (
            "text, watermark, logo, letters, blurry, low quality, distortion, "
            "cgi, 3d render, illustration, cartoon, anime, synthetic, plastic, "
            "over-processed, oversaturated, fake, staged, "
            "advertisement, sale banner, phone number, price tag"
        )
        return positive, negative

    def _generate_with_stability(self, prompt_data: tuple, platform: str) -> Optional[Image.Image]:
        """
        Generate image using NanoBanana text-to-image.
        (Method name kept for compatibility.)
        """
        prompt, negative_prompt = prompt_data
        dims = self.dimensions.get(platform, self.dimensions["default"])

        aspect_ratio = "1:1"
        if dims["width"] == 1080 and dims["height"] == 1920:
            aspect_ratio = "9:16"
        elif dims["width"] == 1080 and dims["height"] == 1350:
            aspect_ratio = "4:5"
        elif dims["width"] == 1200 and dims["height"] == 675:
            aspect_ratio = "16:9"

        try:
            provider = get_image_provider()
            provider.aspect_ratio = aspect_ratio

            result = provider.generate(
                prompt=prompt,
                negative_prompt=negative_prompt,
            )

            image = Image.open(io.BytesIO(result.bytes)).convert("RGB")
            return image

        except Exception as e:
            logger.error(f"Image provider error: {e}")
            return None
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            return None

    def _add_branding_overlay(self, image: Image.Image, article: Dict) -> Image.Image:
        """
        Add subtle branding elements to the image.
        """
        try:
            branded = image.copy()
            draw = ImageDraw.Draw(branded, "RGBA")

            width, height = branded.size

            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            gradient_height = height // 4
            for y in range(gradient_height):
                alpha = int((y / gradient_height) * 120)
                overlay_draw.rectangle(
                    [(0, height - gradient_height + y), (width, height - gradient_height + y + 1)],
                    fill=(0, 78, 65, alpha),
                )

            branded = Image.alpha_composite(branded.convert("RGBA"), overlay)

            accent_width = 80
            accent_height = 8

            colors = [
                (0, 107, 56, 60),
                (255, 255, 255, 60),
                (0, 0, 0, 60),
            ]

            x_start = width - accent_width - 30
            y_start = 30

            for i, color in enumerate(colors):
                draw.rectangle(
                    [
                        (x_start + i * (accent_width // 3), y_start),
                        (x_start + (i + 1) * (accent_width // 3), y_start + accent_height),
                    ],
                    fill=color,
                )

            return branded.convert("RGB")

        except Exception as e:
            logger.warning(f"Failed to add branding overlay: {e}")
            return image

    def _prepare_image_data(self, image, article: Dict) -> Dict:
        """Prepare final image data for storage. Accepts PIL Image or raw bytes."""
        if isinstance(image, (bytes, bytearray)):
            pil_image = Image.open(io.BytesIO(image)).convert("RGB")
        else:
            pil_image = image

        buffer = io.BytesIO()
        pil_image.save(buffer, format="PNG", quality=95)
        image_bytes = buffer.getvalue()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = "".join(c if c.isalnum() else "_" for c in article["title"][:30])
        filename = f"post_{safe_title}_{timestamp}.png"

        return {
            "image_bytes": image_bytes,
            "image_base64": base64.b64encode(image_bytes).decode("utf-8"),
            "filename": filename,
            "format": "PNG",
            "width": pil_image.width,
            "height": pil_image.height,
            "size_kb": len(image_bytes) / 1024,
        }


class SimpleBackgroundGenerator:
    """
    Generate simple, professional gradient backgrounds (no AI needed)
    Useful for text-heavy posts or when AI generation fails
    """

    def __init__(self):
        self.brand_colors = {
            "primary": (0, 78, 65),
            "secondary": (209, 174, 74),
            "white": (255, 255, 255),
        }

    def generate_gradient_background(
        self,
        width: int = 1024,
        height: int = 1024,
        style: Literal["elegant", "modern", "vibrant"] = "elegant",
    ) -> Image.Image:
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)

        if style == "elegant":
            for y in range(height):
                ratio = y / height
                r = int(self.brand_colors["primary"][0] * (1 - ratio) + self.brand_colors["secondary"][0] * ratio)
                g = int(self.brand_colors["primary"][1] * (1 - ratio) + self.brand_colors["secondary"][1] * ratio)
                b = int(self.brand_colors["primary"][2] * (1 - ratio) + self.brand_colors["secondary"][2] * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

        elif style == "modern":
            for y in range(height):
                ratio = y / height
                intensity = int(0 * (1 - ratio) + 30 * ratio)
                draw.line([(0, y), (width, y)], fill=(intensity, intensity + 20, intensity + 10))

        elif style == "vibrant":
            for y in range(height):
                ratio = y / height
                r = int(20 + 100 * ratio)
                g = int(80 + 50 * ratio)
                b = int(100 + 20 * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

        return image