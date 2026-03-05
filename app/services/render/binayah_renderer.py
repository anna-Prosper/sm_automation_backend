from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Optional, Set, Iterable, Tuple, Union

from PIL import Image, ImageDraw, ImageFont


@dataclass
class BinayahPosterInputs:
    headline: str
    website_url: str = "Binayahproperties.com"
    green_words: Set[str] = None
    gold_words: Set[str] = None

    # One of these can be provided:
    background_image_bytes: Optional[bytes] = None
    background_image_path: Optional[str] = None

    # Optional logo
    logo_path: Optional[str] = None

    # Output controls
    width: int = 1080
    height: int = 1350
    format: str = "PNG"


def parse_keywords_string(value: Optional[str]) -> Set[str]:
    """
    Accepts:
      - "Luxury, Dubai, ROI"
      - "Luxury | Dubai | ROI"
      - "Luxury\nDubai\nROI"
    Returns a set of normalized keywords.
    """
    if not value:
        return set()

    # Split on comma, pipe, newline, semicolon
    parts = re.split(r"[,\|\n;]+", value)
    cleaned = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        # normalize internal spaces
        s = re.sub(r"\s+", " ", s)
        cleaned.append(s)

    # de-dup
    seen = set()
    out = set()
    for k in cleaned:
        key = k.lower()
        if key in seen:
            continue
        seen.add(key)
        out.add(k)
    return out


def render_binayah_poster(
    inputs: BinayahPosterInputs,
    return_bytes: bool = True,
    output_path: Optional[str] = None,
) -> Union[bytes, str]:
    """
    Renders a Binayah-style poster:
      - resized background image
      - dark gradient overlay for readability
      - headline
      - keyword chips (green + gold)
      - website footer
      - optional logo at top-left

    If return_bytes=True -> returns PNG bytes
    Else -> saves to output_path and returns output_path
    """
    img = _load_background(inputs)
    img = _cover_resize(img, (inputs.width, inputs.height)).convert("RGBA")

    # Add readability overlay (top->bottom)
    overlay = _make_vertical_overlay(inputs.width, inputs.height)
    canvas = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(canvas)

    # Fonts
    font_head = _load_font(preferred=("DejaVuSans-Bold.ttf", "arialbd.ttf"), size=64)
    font_sub = _load_font(preferred=("DejaVuSans.ttf", "arial.ttf"), size=30)
    font_chip = _load_font(preferred=("DejaVuSans-Bold.ttf", "arialbd.ttf"), size=28)
    font_footer = _load_font(preferred=("DejaVuSans.ttf", "arial.ttf"), size=28)

    # Colors
    WHITE = (255, 255, 255, 255)
    MUTED = (230, 230, 230, 255)
    GREEN = (0, 160, 140, 255)    # Binayah-ish green
    GOLD = (212, 175, 55, 255)    # Gold
    CHIP_TEXT = (255, 255, 255, 255)

    # Layout
    pad = 70
    top_y = 90

    # Optional logo
    if inputs.logo_path:
        try:
            canvas = _paste_logo(canvas, inputs.logo_path, x=pad, y=top_y, max_w=220, max_h=90)
            draw = ImageDraw.Draw(canvas)
        except Exception:
            pass

    # Headline block
    headline = (inputs.headline or "").strip()
    if not headline:
        headline = " "

    headline_box_w = inputs.width - 2 * pad
    headline_lines = _wrap_text(draw, headline, font_head, max_width=headline_box_w)
    headline_y = 220
    _draw_multiline_text(
        draw,
        (pad, headline_y),
        headline_lines,
        font=font_head,
        fill=WHITE,
        line_spacing=10,
    )

    # Keywords chips
    green_words = inputs.green_words or set()
    gold_words = inputs.gold_words or set()

    chips_y = headline_y + _multiline_height(draw, headline_lines, font_head, 10) + 40
    chips_x = pad

    # Render green then gold
    chips_x, chips_y = _draw_chips(
        draw=draw,
        x=chips_x,
        y=chips_y,
        max_width=inputs.width - pad,
        words=_sorted_words(green_words),
        font=font_chip,
        fill_bg=GREEN,
        fill_text=CHIP_TEXT,
        pad_x=18,
        pad_y=10,
        gap_x=14,
        gap_y=14,
        radius=18,
    )

    chips_x, chips_y = _draw_chips(
        draw=draw,
        x=pad,
        y=chips_y + 18,
        max_width=inputs.width - pad,
        words=_sorted_words(gold_words),
        font=font_chip,
        fill_bg=GOLD,
        fill_text=(35, 35, 35, 255),
        pad_x=18,
        pad_y=10,
        gap_x=14,
        gap_y=14,
        radius=18,
    )

    # Footer website
    footer_text = (inputs.website_url or "").strip()
    if footer_text:
        footer_y = inputs.height - 90
        draw.text((pad, footer_y), footer_text, font=font_footer, fill=MUTED)

    # Output
    if return_bytes:
        out = io.BytesIO()
        canvas.convert("RGBA").save(out, format=inputs.format)
        return out.getvalue()

    if not output_path:
        raise ValueError("output_path is required when return_bytes=False")

    canvas.convert("RGBA").save(output_path)
    return output_path


def _load_background(inputs: BinayahPosterInputs) -> Image.Image:
    if inputs.background_image_bytes:
        return Image.open(io.BytesIO(inputs.background_image_bytes)).convert("RGB")
    if inputs.background_image_path:
        return Image.open(inputs.background_image_path).convert("RGB")
    # fallback: plain background
    return Image.new("RGB", (inputs.width, inputs.height), (20, 20, 20))


def _cover_resize(img: Image.Image, target: Tuple[int, int]) -> Image.Image:
    """Resize to cover target size (like CSS background-size: cover)."""
    tw, th = target
    iw, ih = img.size
    if iw == 0 or ih == 0:
        return Image.new("RGB", target, (20, 20, 20))

    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    resized = img.resize((nw, nh), Image.LANCZOS)

    left = (nw - tw) // 2
    top = (nh - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def _make_vertical_overlay(w: int, h: int) -> Image.Image:
    """
    Dark overlay gradient for readability.
    """
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = overlay.load()

    # Darker at top + bottom, lighter at middle
    for y in range(h):
        # alpha curve
        if y < h * 0.45:
            a = int(190 * (1 - (y / (h * 0.45))))
        elif y > h * 0.75:
            a = int(160 * ((y - h * 0.75) / (h * 0.25)))
        else:
            a = 35
        a = max(0, min(220, a))
        for x in range(w):
            px[x, y] = (0, 0, 0, a)

    return overlay


def _load_font(preferred: Iterable[str], size: int) -> ImageFont.FreeTypeFont:
    for name in preferred:
        try:
            return ImageFont.truetype(name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int):
    words = re.split(r"\s+", text.strip())
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if _text_width(draw, test, font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    fixed = []
    for line in lines:
        if _text_width(draw, line, font) <= max_width:
            fixed.append(line)
            continue
        fixed.extend(_hard_wrap(draw, line, font, max_width))
    return fixed


def _hard_wrap(draw, text, font, max_width):
    out = []
    cur = ""
    for ch in text:
        test = cur + ch
        if _text_width(draw, test, font) <= max_width:
            cur = test
        else:
            if cur:
                out.append(cur)
            cur = ch
    if cur:
        out.append(cur)
    return out


def _text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _text_height(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _draw_multiline_text(draw, xy, lines, font, fill, line_spacing=8):
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += _text_height(draw, line, font) + line_spacing


def _multiline_height(draw, lines, font, line_spacing):
    h = 0
    for i, line in enumerate(lines):
        h += _text_height(draw, line, font)
        if i < len(lines) - 1:
            h += line_spacing
    return h


def _sorted_words(words: Set[str]) -> list[str]:
    # stable, nice ordering
    return sorted([w.strip() for w in words if w.strip()], key=lambda s: (len(s), s.lower()))


def _draw_chips(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    max_width: int,
    words: list[str],
    font: ImageFont.ImageFont,
    fill_bg: tuple,
    fill_text: tuple,
    pad_x: int = 16,
    pad_y: int = 8,
    gap_x: int = 12,
    gap_y: int = 12,
    radius: int = 16,
):
    cur_x, cur_y = x, y

    for word in words:
        text_w = _text_width(draw, word, font)
        text_h = _text_height(draw, word, font)

        chip_w = text_w + 2 * pad_x
        chip_h = text_h + 2 * pad_y

        # wrap to next line if needed
        if cur_x + chip_w > max_width:
            cur_x = x
            cur_y += chip_h + gap_y

        # Draw rounded rect
        _rounded_rect(draw, (cur_x, cur_y, cur_x + chip_w, cur_y + chip_h), radius, fill_bg)

        # Text centered
        text_x = cur_x + pad_x
        text_y = cur_y + pad_y - 2
        draw.text((text_x, text_y), word, font=font, fill=fill_text)

        cur_x += chip_w + gap_x

    return cur_x, cur_y


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill)
    except Exception:
        draw.rectangle(box, fill=fill)


def _paste_logo(base: Image.Image, logo_path: str, x: int, y: int, max_w: int, max_h: int) -> Image.Image:
    logo = Image.open(logo_path).convert("RGBA")
    lw, lh = logo.size
    if lw == 0 or lh == 0:
        return base

    scale = min(max_w / lw, max_h / lh)
    nw, nh = int(lw * scale), int(lh * scale)
    logo = logo.resize((nw, nh), Image.LANCZOS)

    base = base.copy()
    base.paste(logo, (x, y), logo)
    return base
