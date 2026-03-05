"""
Shared rendering helpers used by all Binayah templates.
Fonts · text wrapping · visual effects · IO utilities
"""
from __future__ import annotations

import os
import math
import uuid
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Set, Tuple
import logging

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.services.templates.base_template import (
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE, BLACK, RED,
)

# ---------------------------------------------------------------------------
# Shared logo resolver — works in any environment (local, Docker, DO droplet)
# ---------------------------------------------------------------------------
# In the Docker image the Dockerfile does:
#   COPY app    /app/app
#   COPY assets /app/assets   ← logo lives here at /app/assets/logo_w.png
#
# This file lives at  /app/app/services/templates/rendering_helpers.py
# so parents[3] resolves to /app inside the container,
# and to the project root locally.
# ---------------------------------------------------------------------------

_THIS_DIR = Path(__file__).resolve().parent   # …/templates/
_APP_DIR  = _THIS_DIR.parents[1]              # …/app/  (the Python package root)
_REPO_ROOT = _THIS_DIR.parents[3]             # /app in Docker, or project root locally


def resolve_logo_path(filename: str = "logo_w.png") -> Optional[str]:
    """
    Return the absolute path to a logo asset, or None if not found.

    Search order (first match wins):
      1. LOGO_PATH env var — lets ops override without a redeploy
      2. /app/assets/<filename>              — Docker / production path
      3. <repo_root>/assets/<filename>       — resolved via __file__
      4. <app_dir>/assets/<filename>         — apps/api/app/assets/
      5. <repo_root>/storage/assets/<filename>
      6. Common relative paths as last resort
    """
    # 1. Explicit env override
    env_override = os.environ.get("LOGO_PATH")
    if env_override and os.path.exists(env_override):
        return env_override

    candidates = [
        # 2. Docker absolute path — most important for production
        Path("/app/assets") / filename,
        # 3. Resolved via __file__ — handles any mount / cwd variation
        _REPO_ROOT / "assets" / filename,
        # 4. Alongside the app Python package
        _APP_DIR / "assets" / filename,
        # 5. Shared storage volume
        _REPO_ROOT / "storage" / "assets" / filename,
        # 6. Relative fallbacks (last resort for ad-hoc local runs)
        Path("apps/api/app/assets") / filename,
        Path("apps/api/assets") / filename,
        Path("storage/assets") / filename,
    ]

    for p in candidates:
        if p.exists():
            resolved = str(p)
            logging.getLogger(__name__).debug(f"Logo resolved: {resolved}")
            return resolved

    logging.getLogger(__name__).warning(
        f"Logo '{filename}' not found. Tried: {[str(c) for c in candidates]}"
    )
    return None


# Font loading
logger = logging.getLogger(__name__)

_HEADLINE_FONT_PATHS = [
    "/app/assets/fonts/impact.ttf",
    "/app/assets/fonts/Impact.ttf",
    "/assets/fonts/impact.ttf",
    "/assets/fonts/Impact.ttf",
    "/apps/api/app/assets/fonts/impact.ttf",
    "/app/assets/fonts/impact.ttf",
    "apps/api/app/assets/fonts/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

_BODY_FONT_PATHS = [
    "/app/assets/fonts/Poppins-Light.ttf",
    "/app/assets/fonts/poppins-light.ttf",
    "/assets/fonts/Poppins-Light.ttf",
    "/assets/fonts/poppins-light.ttf",
    "/apps/api/app/assets/fonts/Poppins-Light.ttf",
    "/apps/api/app/assets/fonts/poppins-light.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def load_headline_font(size: int) -> ImageFont.FreeTypeFont:
    """Load headline font with proper fallback chain"""
    for path in _HEADLINE_FONT_PATHS:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                logger.debug(f"Loaded headline font from: {path}")
                return font
            except Exception as e:
                logger.warning(f"Failed to load font {path}: {e}")
                continue
    
    logger.error(f"⚠️ CRITICAL: No headline font found! Using default (will be tiny)")
    logger.error(f"Searched paths: {_HEADLINE_FONT_PATHS}")
    return ImageFont.load_default()


def load_body_font(size: int) -> ImageFont.FreeTypeFont:
    """Load body font with proper fallback chain"""
    for path in _BODY_FONT_PATHS:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                logger.debug(f"Loaded body font from: {path}")
                return font
            except Exception as e:
                logger.warning(f"Failed to load font {path}: {e}")
                continue
    
    logger.error(f"⚠️ CRITICAL: No body font found! Using default (will be tiny)")
    logger.error(f"Searched paths: {_BODY_FONT_PATHS}")
    return ImageFont.load_default()


# Image IO

def load_image_bytes(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data)).convert("RGBA")


def load_image_path(path: str) -> Image.Image:
    with open(path, "rb") as f:
        return Image.open(f).convert("RGBA")


def cover_resize(img: Image.Image, tw: int, th: int) -> Image.Image:
    """CSS background-size:cover — fill target, center crop."""
    sw, sh = img.size
    if sw / sh > tw / th:
        scale = th / sh
        nw = int(sw * scale)
        resized = img.resize((nw, th), Image.LANCZOS)
        left = (nw - tw) // 2
        return resized.crop((left, 0, left + tw, th))
    else:
        scale = tw / sw
        nh = int(sh * scale)
        resized = img.resize((tw, nh), Image.LANCZOS)
        top = (nh - th) // 2
        return resized.crop((0, top, tw, top + th))


def save_poster(img: Image.Image, output_dir: str, prefix: str) -> str:
    """DEPRECATED: Use image_to_bytes() instead. This function is kept for backward compatibility."""
    os.makedirs(output_dir, exist_ok=True)
    fname = f"post_{prefix}_{uuid.uuid4().hex[:12]}.png"
    path = os.path.join(output_dir, fname)
    img.convert("RGB").save(path, quality=95)
    return path


def image_to_bytes(img: Image.Image, format: str = "PNG", quality: int = 95) -> bytes:
    """
    Convert PIL Image to bytes for S3 upload.
    
    Args:
        img: PIL Image object
        format: Image format (PNG, JPEG, etc.)
        quality: Quality for JPEG (1-95)
    
    Returns:
        bytes: Image data as bytes
    """
    buffer = BytesIO()
    if format.upper() in ["JPEG", "JPG"]:
        img.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    else:
        img.convert("RGB").save(buffer, format=format, quality=quality)
    buffer.seek(0)
    return buffer.getvalue()


def generate_filename(prefix: str = "post", extension: str = "png") -> str:
    """Generate a unique filename with timestamp and UUID."""
    from datetime import datetime
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    unique_id = uuid.uuid4().hex[:12]
    return f"{prefix}_{timestamp}_{unique_id}.{extension}"


# Visual effects

def apply_bottom_fade(
    img: Image.Image,
    fade_ratio: float = 0.75,
    color: Tuple[int, ...] = (0, 0, 0),
) -> Image.Image:
    """Gradient from *color* at bottom to transparent."""
    w, h = img.size
    fade_h = int(h * fade_ratio)
    gradient = Image.new("L", (1, fade_h), 0)
    for y in range(fade_h):
        gradient.putpixel((0, y), int(255 * y / (fade_h - 1)))
    gradient = gradient.resize((w, fade_h))

    overlay = Image.new("RGBA", (w, fade_h), color + (255,) if len(color) == 3 else color)
    overlay.putalpha(gradient)

    out = img.copy()
    out.paste(overlay, (0, h - fade_h), overlay)
    return out


def apply_vignette(
    img: Image.Image,
    strength: float = 0.65,
    blur: int = 80,
) -> Image.Image:
    """Dark-edges vignette."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    cx, cy = w / 2, h / 2
    max_d = math.sqrt(cx * cx + cy * cy)

    for y in range(h):
        for x in range(w):
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            v = d / max_d
            mask.putpixel((x, y), int(255 * (v ** 1.8) * strength))

    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    vig = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    vig.putalpha(mask)

    out = img.copy()
    out.alpha_composite(vig)
    return out


def darken_image(img: Image.Image, factor: float = 0.4) -> Image.Image:
    """Darken: factor 0=black, 1=original."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, int(255 * (1 - factor))))
    out = img.copy()
    out.alpha_composite(overlay)
    return out


def tint_image(
    img: Image.Image,
    color: Tuple[int, int, int] = (0, 78, 65),
    strength: float = 0.15,
) -> Image.Image:
    overlay = Image.new("RGBA", img.size, color + (int(255 * strength),))
    out = img.copy()
    out.alpha_composite(overlay)
    return out


# Text layout engine

def normalize_headline(headline: str, max_words: int = 16) -> str:
    words = headline.strip().split()[:max_words]
    return " ".join(words).upper()


def _wrap_tokens(
    draw: ImageDraw.ImageDraw,
    tokens: List[str],
    font: ImageFont.FreeTypeFont,
    max_w: int,
    max_lines: int,
) -> List[List[str]]:
    """Greedy line-wrapping."""
    lines: list[list[str]] = []
    idx = 0
    for _ in range(max_lines):
        if idx >= len(tokens):
            break
        cur: list[str] = []
        while idx < len(tokens):
            test = " ".join(cur + [tokens[idx]]) if cur else tokens[idx]
            if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
                cur.append(tokens[idx])
                idx += 1
            else:
                break
        if not cur:
            cur = [tokens[idx]]
            idx += 1
        lines.append(cur)

    # Truncate with ellipsis
    if idx < len(tokens) and lines:
        last = lines[-1]
        while True:
            test = " ".join(last + ["..."])
            if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
                lines[-1] = last + ["..."]
                break
            if len(last) <= 1:
                lines[-1] = [last[0][: max(1, len(last[0]) - 3)] + "..."]
                break
            last = last[:-1]

    return lines


def fit_headline_font(
    base: Image.Image,
    tokens: List[str],
    box: Tuple[int, int, int, int],
    max_lines: int = 4,
    start_size: int = 150,
    min_size: int = 100,
    line_spacing: int = 6,
):
    draw = ImageDraw.Draw(base)
    x1, y1, x2, y2 = box
    max_w, max_h = x2 - x1, y2 - y1

    size = start_size
    while size >= min_size:
        font = load_headline_font(size)
        lines = _wrap_tokens(draw, tokens, font, max_w, max_lines)
        total_h = len(lines) * (font.size + line_spacing)
        if total_h <= max_h and len(lines) <= max_lines:
            return font, lines
        size -= 2

    font = load_headline_font(min_size)
    lines = _wrap_tokens(draw, tokens, font, max_w, max_lines)
    return font, lines


def draw_colored_headline(
    base: Image.Image,
    headline: str,
    box: Tuple[int, int, int, int],
    gold_words: Set[str],
    red_words: Set[str] | None = None,
    max_words: int = 16,
    max_lines: int = 4,
    start_font_size: int = 50,
    line_spacing: int = 6,
    default_color=WHITE,
    highlight_color=BINAYAH_GOLD,
    negative_color=RED,
    shadow: bool = True,
):
    """
    Draw headline with per-word colour mixing.
    gold_words → highlight_color, red_words → negative_color, rest - default.
    """
    draw = ImageDraw.Draw(base)
    headline = normalize_headline(headline, max_words)
    tokens = headline.split()
    red_words = red_words or set()

    font, lines = fit_headline_font(
        base, tokens, box, max_lines, start_font_size, 50, line_spacing,
    )

    x1, y1, x2, y2 = box
    max_w, max_h = x2 - x1, y2 - y1
    total_h = len(lines) * (font.size + line_spacing)
    y = y1 + (max_h - total_h) // 2

    for line_tokens in lines:
        space_w = draw.textbbox((0, 0), " ", font=font)[2]
        word_ws = [draw.textbbox((0, 0), w, font=font)[2] for w in line_tokens]
        line_w = sum(word_ws) + space_w * max(0, len(line_tokens) - 1)
        x = x1 + (max_w - line_w) // 2

        for i, w in enumerate(line_tokens):
            if w in red_words:
                c = negative_color
            elif w in gold_words:
                c = highlight_color
            else:
                c = default_color

            # Optional shadow
            if shadow:
                draw.text((x + 3, y + 3), w, font=font, fill=(0, 0, 0, 180))
            draw.text((x, y), w, font=font, fill=c)

            x += word_ws[i]
            if i < len(line_tokens) - 1:
                x += space_w

        y += font.size + line_spacing


def draw_url(
    base: Image.Image,
    text: str,
    center: Tuple[int, int] = (540, 1010),
    font_size: int = 30,
    color=WHITE,
):
    draw = ImageDraw.Draw(base)
    font = load_body_font(font_size)
    bb = draw.textbbox((0, 0), text.strip(), font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = center[0] - tw // 2
    y = center[1] - th // 2
    draw.text((x, y), text.strip(), font=font, fill=color)


def draw_location_tag(
    base: Image.Image,
    location: str,
    position: Tuple[int, int] = (540, 650),
    font_size: int = 30,
):
    """Small location tag above headline."""
    if not location:
        return
    draw = ImageDraw.Draw(base)
    font = load_body_font(font_size)
    text = f"📍 {location}"
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    x = position[0] - tw // 2
    # Background pill
    pad = 8
    draw.rounded_rectangle(
        (x - pad, position[1] - pad, x + tw + pad, position[1] + bb[3] - bb[1] + pad),
        radius=12,
        fill=(0, 0, 0, 140),
    )
    draw.text((x, position[1]), text, font=font, fill=WHITE)


def draw_bottom_logo(
    base: Image.Image,
    logo_path: str,
    center: tuple[int, int] = (540, 1000),
    max_w: int = 170,
):
    if not logo_path or not os.path.exists(logo_path):
        return

    logo = load_image_path(logo_path)

    # keep aspect ratio
    w, h = logo.size
    scale = max_w / float(w)
    nw, nh = int(w * scale), int(h * scale)
    logo = logo.resize((nw, nh), Image.LANCZOS)

    x = center[0] - nw // 2
    y = center[1] - nh // 2
    base.alpha_composite(logo, (x, y))
