"""
Breaking News Template — Binayah
High-impact broadcast style for urgent market news, records, and alerts.

Design language:
  • Full-bleed image (darkened)
  • Top-left "MARKET UPDATE" tag badge (red pill)
  • Very large, condensed Impact headline — white/gold/red
  • Bold teal + gold bottom bar panel
  • Website URL centered
  • High drama — bold contrast — made for thumb-stopping engagement
"""
import os
from PIL import Image, ImageDraw

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE, BLACK, RED,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    apply_vignette, darken_image, tint_image, apply_bottom_fade, apply_diagonal_gradient,
    draw_colored_headline, draw_url, draw_location_tag,
    load_headline_font, load_body_font,
    resolve_logo_path,
)

# Tag options based on sentiment
_SENTIMENT_TAGS = {
    "negative": ("BREAKING", (200, 30, 30)),
    "positive": ("MARKET UPDATE", (0, 140, 80)),
    "neutral": ("LATEST NEWS", (0, 78, 65)),
}


class BreakingNewsTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "breaking_news"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        hl = headline.lower()
        high = [
            "crash", "surge", "record", "breaking", "plunge", "boom",
            "soar", "spike", "unprecedented", "warning", "alert",
            "slump", "bubble", "crisis",
        ]
        mid = [
            "price", "market", "growth", "regulation",
            "mortgage", "rent", "yield", "?",
        ]

        score = 0.35
        if "?" in headline:
            score = 0.72

        for w in high:
            if w in hl:
                score = 0.96
                break
        for w in mid:
            if w in hl:
                score = max(score, 0.68)

        if sentiment == "negative":
            score = min(score * 1.25, 1.0)
        return score

    def render(self, inputs: TemplateInputs, output_dir: str = "apps/api/app/storage/renders") -> str:
        """DEPRECATED: Use render_to_bytes() instead."""
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "breaking")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(self, inputs: TemplateInputs, sentiment: str = "neutral") -> Image.Image:
        W, H = self._get_dimensions()   # 1080 × 1350

        # Background
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
            bg = darken_image(bg, factor=0.5)
        else:
            bg = Image.new("RGBA", (W, H), (8, 8, 12, 255))

        bg = apply_vignette(bg, strength=0.2, blur=50)
        bg = apply_bottom_fade(bg, fade_ratio=0.20, color=(0, 0, 0))

        draw = ImageDraw.Draw(bg)

        # Logo top-left
        logo_path = inputs.logo_path or self._find_logo()
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            logo = logo.resize((300, 300), Image.LANCZOS)
            bg.alpha_composite(logo, (10, -70))


        red_words = inputs.red_words or set()
        if red_words & {"CRASH", "DROP", "FALL", "DECLINE", "WARNING", "FRAUD", "BUBBLE", "SLUMP"}:
            tag_label, tag_color = _SENTIMENT_TAGS["negative"]
        else:
            tag_label, tag_color = _SENTIMENT_TAGS["neutral"]

        font_tag = load_body_font(26)
        bb = draw.textbbox((0, 0), tag_label, font=font_tag)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad_x, pad_y = 18, 40
        rx = W - tw - pad_x * 2 - 30
        ry = 34
        rw = W - 30
        rh = ry + th + pad_y * 2

        draw.rounded_rectangle([(rx, ry), (rw, rh)], radius=6, fill=(*tag_color, 230))
        draw.text((rx + pad_x, ry + pad_y), tag_label, font=font_tag, fill=WHITE)

        # Thin gold bar (horizontal mid-divider near bottom of image)
        bar_y = int(H * 0.70)
        draw.rectangle([(0, bar_y), (W, bar_y + 5)], fill=BINAYAH_GOLD)

        # Location tag 
        if inputs.location_tag:
            draw_location_tag(bg, inputs.location_tag, position=(540, bar_y - 55))

        # Headline
        draw_colored_headline(
            bg,
            headline=inputs.headline,
            box=(40, bar_y + 20, W - 40, H - 110),
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=14,
            max_lines=4,
            start_font_size=60,
            line_spacing=8,
            highlight_color=BINAYAH_GOLD,
            negative_color=RED,
            shadow=True,
        )

        # Gold bottom strip
        draw.rectangle([(0, H - 58), (W, H)], fill=BINAYAH_TEAL)
        draw.rectangle([(0, H - 58), (W, H - 54)], fill=BINAYAH_GOLD)

        # Website URL
        draw_url(bg, inputs.website_url, center=(540, H - 28), font_size=26,
                 color=(*BINAYAH_GOLD[:3], 220))

        return bg

    @staticmethod
    def _find_logo() -> str | None:
        return resolve_logo_path("logo_w.png")