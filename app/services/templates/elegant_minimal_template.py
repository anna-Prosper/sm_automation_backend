"""
Elegant Minimal Template — Binayah
"""
import os
from typing import Set
from io import BytesIO
from PIL import Image, ImageDraw

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    draw_colored_headline, draw_url,
    load_body_font,
    resolve_logo_path,
)


class ElegantMinimalTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "elegant_minimal"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        hl = headline.lower()
        high = ["lifestyle", "community", "design", "interior",
                "award", "best", "top", "beautiful", "stunning"]
        mid  = ["guide", "tips", "how", "trend", "update",
                "new", "open", "project", "development"]

        score = 0.45
        for w in high:
            if w in hl:
                score = 0.85
                break
        for w in mid:
            if w in hl:
                score = max(score, 0.65)

        if sentiment == "negative":
            score *= 0.5
        return score

    def render(
        self,
        inputs: TemplateInputs,
        output_dir: str = "apps/api/app/storage/renders",
    ) -> str:
        """DEPRECATED: Use render_to_bytes() instead"""
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "elegant")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        """NEW: Render directly to bytes for S3 upload (no file I/O)"""
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(self, inputs: TemplateInputs) -> Image.Image:
        """Shared rendering logic used by both render() and render_to_bytes()"""
        W, H = self._get_dimensions()
        BAR_H = 350 

        # Background
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
        else:
            bg = Image.new("RGBA", (W, H), (40, 40, 40, 255))

        # Solid teal bar at bottom
        bar = Image.new("RGBA", (W, BAR_H), BINAYAH_TEAL)
        bg.paste(bar, (0, H - BAR_H))

        # Gold accent line at top of bar
        draw = ImageDraw.Draw(bg)
        draw.rectangle(
            (0, H - BAR_H, W, H - BAR_H + 4),
            fill=BINAYAH_GOLD,
        )

        # Logo with pill background
        logo_path = inputs.logo_path or self._find_logo()
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            max_logo_w = 230
            lw, lh = logo.size
            scale = max_logo_w / lw
            logo = logo.resize((int(lw * scale), int(lh * scale)), Image.LANCZOS)
            bg.alpha_composite(logo, (30, 30))

        # Location tag inside bar
        if inputs.location_tag:
            font = load_body_font(30)
            tag = f"📍 {inputs.location_tag}"
            bb = draw.textbbox((0, 0), tag, font=font)
            tw = bb[2] - bb[0]
            draw.text(
                ((W - tw) // 2, H - BAR_H + 16),
                tag,
                font=font,
                fill=BINAYAH_GOLD,
            )
            headline_y_start = H - BAR_H + 30
        else:
            headline_y_start = H - BAR_H + 20

        # Headline in the bar
        draw_colored_headline(
            bg,
            headline=inputs.headline,
            box=(50, headline_y_start, 1030, H - 100),
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=14,
            max_lines=3,
            start_font_size=50,
            line_spacing=5,
            default_color=WHITE,
            highlight_color=BINAYAH_GOLD,
            shadow=False,
        )

        # Website URL
        draw_url(
            bg,
            inputs.website_url,
            center=(540, 1280),
            font_size=30,
            color=(255, 255, 255, 160),
        )

        return bg

    @staticmethod
    def _find_logo() -> str | None:
        return resolve_logo_path("logo.png")