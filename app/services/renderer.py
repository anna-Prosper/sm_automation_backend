from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
import requests


@dataclass
class RenderInputs:
    template_id: str
    headline: str
    subtext: Optional[str] = None
    watermark_text: Optional[str] = None

    background_image_bytes: Optional[bytes] = None
    background_path: Optional[str] = None
    background_color: Optional[str] = None

    label: Optional[str] = None
    big_word: Optional[str] = None
    bullets: Optional[List[str]] = None
    metric_value: Optional[str] = None
    metric_label: Optional[str] = None
    stats_row: Optional[List[str]] = None


def load_brand_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> Tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    a = int(max(0, min(1, alpha)) * 255)
    return (r, g, b, a)


def open_image_from_bytes(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    return img.convert("RGBA")


def center_crop_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def resize_to_canvas(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    return img.resize(size, Image.Resampling.LANCZOS)


def apply_bottom_gradient(canvas: Image.Image, strength: float = 0.72, start_ratio: float = 0.48) -> Image.Image:
    w, h = canvas.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    start_y = int(h * start_ratio)
    for y in range(start_y, h):
        t = (y - start_y) / max(1, (h - start_y - 1))
        a = int(255 * (strength * t))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, a))

    return Image.alpha_composite(canvas, overlay)


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    if not words:
        return [""]

    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def fit_text(draw: ImageDraw.ImageDraw, text: str, font_path: str, box: Tuple[int, int, int, int],
             start: int, min_: int, max_lines: int) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    x1, y1, x2, y2 = box
    max_w = x2 - x1
    max_h = y2 - y1

    size = start
    while size >= min_:
        font = load_font(font_path, size)
        lines = wrap_text(draw, text, font, max_w)
        if len(lines) <= max_lines:
            line_h = int(size * 1.08)
            if line_h * len(lines) <= max_h:
                return font, lines
        size -= 2

    font = load_font(font_path, min_)
    lines = wrap_text(draw, text, font, max_w)[:max_lines]
    return font, lines


def draw_centered(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], lines: List[str],
                  font: ImageFont.FreeTypeFont, fill: Tuple[int, int, int, int]):
    x1, y1, x2, y2 = box
    bw = x2 - x1
    bh = y2 - y1
    line_h = int(font.size * 1.08)
    total_h = line_h * len(lines)
    y = y1 + (bh - total_h) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = x1 + (bw - tw) // 2
        draw.text((x, y + i * line_h), line, font=font, fill=fill)


def paste_logo(canvas: Image.Image, logo_path: str):
    if not os.path.exists(logo_path):
        return
    logo = Image.open(logo_path).convert("RGBA")
    max_w = 110
    if logo.size[0] > max_w:
        scale = max_w / logo.size[0]
        logo = logo.resize((int(logo.size[0] * scale), int(logo.size[1] * scale)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (40, 40))


def render_image(brand_cfg: Dict[str, Any], inputs: RenderInputs) -> Image.Image:
    W = int(brand_cfg["canvas"]["width"])
    H = int(brand_cfg["canvas"]["height"])
    colors = brand_cfg["colors"]
    typo = brand_cfg["typography"]

    # background
    if inputs.background_image_bytes:
        bg = open_image_from_bytes(inputs.background_image_bytes)
        bg = resize_to_canvas(center_crop_square(bg), (W, H))
    else:
        bg = Image.new("RGBA", (W, H), hex_to_rgba(inputs.background_color or colors["bg_dark"], 1.0))

    canvas = bg

    if inputs.template_id in ("news_card_v1", "market_mood_v1"):
        canvas = apply_bottom_gradient(canvas)

    draw = ImageDraw.Draw(canvas)

    # logo (optional)
    paste_logo(canvas, brand_cfg["brand"]["logo_path"])

    if inputs.template_id == "news_card_v1":
        headline_box = (80, 520, 1000, 820)
        font_h, lines_h = fit_text(draw, inputs.headline.strip(), typo["headline_font"], headline_box, 84, 56, 4)
        draw_centered(draw, headline_box, lines_h, font_h, hex_to_rgba(colors["text_primary"], 1.0))

        if inputs.subtext:
            sub_box = (120, 820, 960, 890)
            font_s, lines_s = fit_text(draw, inputs.subtext.strip(), typo["body_font"], sub_box, 36, 28, 1)
            draw_centered(draw, sub_box, lines_s, font_s, hex_to_rgba(colors["text_muted"], 1.0))

    else:
        # fallback: simple text
        box = (80, 300, 1000, 780)
        font_h, lines_h = fit_text(draw, inputs.headline.strip(), typo["headline_font"], box, 84, 56, 6)
        draw_centered(draw, box, lines_h, font_h, hex_to_rgba(colors["text_primary"], 1.0))

    # watermark
    watermark = inputs.watermark_text or brand_cfg["brand"]["watermark_text"]
    font_w = load_font(typo["body_font"], 26)
    bbox = draw.textbbox((0, 0), watermark, font=font_w)
    draw.text((W - 40 - (bbox[2]-bbox[0]), H - 80), watermark, font=font_w,
              fill=hex_to_rgba(colors["text_muted"], 0.7))

    return canvas


def render_post_to_file(brand_config_path: str, inputs: RenderInputs, output_dir: str, filename: Optional[str] = None) -> str:
    cfg = load_brand_config(brand_config_path)
    img = render_image(cfg, inputs).convert("RGBA")

    os.makedirs(output_dir, exist_ok=True)
    if not filename:
        filename = f"{inputs.template_id}_{abs(hash(inputs.headline)) % 10_000_000}.png"

    out_path = os.path.join(output_dir, filename)
    img.save(out_path, format="PNG", optimize=True)
    try:
        from app.core.config import STORAGE_DIR

        rel = os.path.relpath(out_path, STORAGE_DIR).replace(os.sep, "/")
        return f"/storage/{rel}"
    except Exception:
        return out_path


from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os, textwrap, random
import requests
from io import BytesIO

CANVAS = (1080, 1080)

def _load_local(path: str) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    return img

def _fetch_bg_from_provider(query: str, preferred: str = "auto") -> Image.Image:



    raise NotImplementedError

def _fit_cover(img: Image.Image, size=(1080,1080)) -> Image.Image:
    w,h = img.size
    tw,th = size
    scale = max(tw/w, th/h)
    nw, nh = int(w*scale), int(h*scale)
    img = img.resize((nw,nh))
    left = (nw - tw)//2
    top = (nh - th)//2
    return img.crop((left, top, left+tw, top+th))

def render_breaking_news_poster(
    headline: str,
    brand_logo_path: str,
    hero_asset_path: str | None,
    inset_asset_path: str | None,
    bg_image: Image.Image,
    font_bold_path: str,
    out_path: str,
):
    base = _fit_cover(bg_image.convert("RGBA"), CANVAS)

    # Add cinematic contrast + vignette
    vignette = Image.new("RGBA", CANVAS, (0,0,0,0))
    vd = ImageDraw.Draw(vignette)
    vd.rectangle([0,0,1080,1080], fill=(0,0,0,40))
    vignette = vignette.filter(ImageFilter.GaussianBlur(16))
    base = Image.alpha_composite(base, vignette)

    draw = ImageDraw.Draw(base)

    # Hero portrait (center big)
    if hero_asset_path and os.path.exists(hero_asset_path):
        hero = _load_local(hero_asset_path)
        hero = _fit_cover(hero, (900, 900))
        # place slightly lower
        hx = (1080 - 900)//2
        hy = 90
        base.alpha_composite(hero, (hx, hy))

    # Chart overlay (simple red down line, bottom-right)
    overlay = Image.new("RGBA", CANVAS, (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    # transparent panel
    od.rectangle([650, 510, 1080, 820], fill=(0,0,0,120))
    # down line
    od.line([(690, 560), (760, 600), (830, 585), (900, 680), (1040, 780)], fill=(255,40,40,255), width=10)
    base = Image.alpha_composite(base, overlay)

    # Inset circle top-right
    if inset_asset_path and os.path.exists(inset_asset_path):
        inset = _load_local(inset_asset_path).resize((220,220))
        mask = Image.new("L", (220,220), 0)
        md = ImageDraw.Draw(mask)
        md.ellipse([0,0,219,219], fill=255)

        circle = Image.new("RGBA", (240,240), (0,0,0,0))
        cd = ImageDraw.Draw(circle)
        cd.ellipse([0,0,239,239], fill=(255,255,255,220))  # ring
        circle.alpha_composite(inset, (10,10), mask=mask)
        base.alpha_composite(circle, (790, 80))

    # Bottom gradient for text readability
    grad = Image.new("RGBA", CANVAS, (0,0,0,0))
    gd = ImageDraw.Draw(grad)
    gd.rectangle([0, 620, 1080, 1080], fill=(0,0,0,180))
    grad = grad.filter(ImageFilter.GaussianBlur(20))
    base = Image.alpha_composite(base, grad)

    # Fonts
    f_big = ImageFont.truetype(font_bold_path, 84)
    f_mid = ImageFont.truetype(font_bold_path, 78)

    # Big headline, multi-line
    lines = textwrap.wrap(headline.upper(), width=18)[:4]
    y = 690
    for i, line in enumerate(lines):
        # make some words green like your sample (simple heuristic)
        fill = (255,255,255,255)
        if i == 0 and "FIRST" in line:
            fill = (0, 255, 160, 255)
        draw.text((70, y), line, font=f_big, fill=fill, stroke_width=3, stroke_fill=(0,0,0,180))
        y += 92

    # Brand logo bottom center
    if os.path.exists(brand_logo_path):
        logo = _load_local(brand_logo_path)
        logo = logo.resize((260, 90))
        base.alpha_composite(logo, ((1080-260)//2, 960))

    base.convert("RGB").save(out_path, quality=95)
