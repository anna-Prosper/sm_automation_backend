"""
Professional Luxury Template — Binayah
"""
import os
from typing import Set
from PIL import Image

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    apply_bottom_fade, apply_vignette,
    draw_colored_headline, draw_url, draw_location_tag, draw_bottom_logo,
    resolve_logo_path,
)


class ProfessionalLuxuryTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "professional_luxury"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        hl = headline.lower()
        high = ["launch", "partner", "announce", "invest", "acquir",
                "expand", "strategic", "record", "milestone", "luxury"]
        mid  = ["buy", "sell", "approve", "billion", "million",
                "villa", "tower", "penthouse", "waterfront"]

        score = 0.5
        for w in high:
            if w in hl:
                score = 0.9
                break
        for w in mid:
            if w in hl:
                score = max(score, 0.7)

        if sentiment == "negative":
            score *= 0.6
        return score

    def render(
        self,
        inputs: TemplateInputs,
        output_dir: str = "apps/api/app/storage/renders",
    ) -> str:
        """DEPRECATED: Use render_to_bytes() instead"""
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "luxury")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        """Render directly to bytes for S3 upload (no file I/O)"""
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(self, inputs: TemplateInputs) -> Image.Image:
        """Shared rendering logic used by both render() and render_to_bytes()"""
        W, H = self._get_dimensions()

        # Background
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
        else:
            bg = Image.new("RGBA", (W, H), BINAYAH_TEAL)

        bg = apply_bottom_fade(bg, fade_ratio=0.78)
        bg = apply_vignette(bg, strength=0.60, blur=80)

        # Logo (top-left)
        logo_path = inputs.logo_path or self._find_logo()
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            logo = logo.resize((300, 300), Image.LANCZOS)
            bg.alpha_composite(logo, (10, -50))

        # Location tag
        if inputs.location_tag:
            draw_location_tag(bg, inputs.location_tag, position=(540, 900))

        #Headline
        draw_colored_headline(
            bg,
            headline=inputs.headline,
            box=(60, 950, 1020, 1250),
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=14,
            max_lines=3,
            start_font_size=50,
            line_spacing=10,
            highlight_color=BINAYAH_GOLD,
            shadow=True,
        )

        logo_path = inputs.logo_path or self._find_logo()
        #draw_bottom_logo(bg, logo_path, center=(540, 990), max_w=160)

        # Website URL
        draw_url(bg, inputs.website_url, center=(540, 1280), font_size=30)

        return bg

    @staticmethod
    def _find_logo() -> str | None:
        return resolve_logo_path("logo_w.png")
