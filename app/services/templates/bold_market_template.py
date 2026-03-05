"""
Bold Market News Template — Binayah
"""
import os
from typing import Set
from PIL import Image

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE, BLACK, RED,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    apply_bottom_fade, apply_vignette, darken_image, tint_image,
    draw_colored_headline, draw_url, draw_location_tag,
    resolve_logo_path,
)


class BoldMarketTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "bold_market"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        hl = headline.lower()
        high = ["crash", "surge", "record", "plunge", "boom",
                "soar", "spike", "unprecedented", "warning", "alert"]
        mid  = ["price", "market", "transaction", "growth",
                "regulation", "mortgage", "rent", "yield"]

        score = 0.4
        if "?" in headline:
            score = 0.75

        for w in high:
            if w in hl:
                score = 0.95
                break
        for w in mid:
            if w in hl:
                score = max(score, 0.7)

        if sentiment == "negative":
            score = min(score * 1.3, 1.0)
        return score

    def render(
        self,
        inputs: TemplateInputs,
        output_dir: str = "apps/api/app/storage/renders",
    ) -> str:
        """DEPRECATED: Use render_to_bytes() instead"""
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "bold")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        """NEW: Render directly to bytes for S3 upload (no file I/O)"""
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(self, inputs: TemplateInputs) -> Image.Image:
        """Shared rendering logic used by both render() and render_to_bytes()"""
        W, H = self._get_dimensions()

        # Background
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
            bg = darken_image(bg, factor=0.35)
            # Teal tint for brand identity
            bg = tint_image(bg, color=(0, 78, 65), strength=0.12)
        else:
            bg = Image.new("RGBA", (W, H), BLACK)

        bg = apply_vignette(bg, strength=0.80, blur=90)

        # Logo
        logo_path = inputs.logo_path or self._find_logo()
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            logo = logo.resize((300, 300), Image.LANCZOS)
            bg.alpha_composite(logo, (10, -50))

        # Location tag
        if inputs.location_tag:
            draw_location_tag(bg, inputs.location_tag, position=(540, 920))

        # Headline
        draw_colored_headline(
            bg,
            headline=inputs.headline,
            box=(40, 950, 1040, 1255),
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=14,
            max_lines=4,
            start_font_size=50,
            line_spacing=8,
            highlight_color=BINAYAH_GOLD,
            negative_color=RED,
            shadow=True,
        )

        # Website URL
        draw_url(bg, inputs.website_url, center=(540, 1270), font_size=30)

        return bg

    @staticmethod
    def _find_logo() -> str | None:
        return resolve_logo_path("logo_w.png")
