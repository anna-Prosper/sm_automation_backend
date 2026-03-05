"""
Luxury Dark Template — Binayah
Premium editorial style for high-end property and investment news.

Design language:
  • Near-black background (image heavily darkened)
  • Thin horizontal gold rule dividing content zones
  • Large Impact headline with generous spacing
  • Thin Montserrat body caption below headline
  • Gold bottom border strip
  • Logo centered in top-right corner
  • Clean negative space — less is more
"""
import os
from PIL import Image, ImageDraw

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE, BLACK,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    apply_vignette, darken_image, tint_image, apply_diagonal_gradient,
    draw_colored_headline, draw_url, draw_location_tag,
    load_body_font,
    resolve_logo_path,
)


class LuxuryDarkTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "luxury_dark"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        hl = headline.lower()
        high = [
            "luxury", "penthouse", "mansion", "villa", "waterfront",
            "premium", "exclusive", "landmark", "iconic", "trophy",
            "billion", "record", "ultra", "prime",
        ]
        mid = [
            "invest", "launch", "partner", "milestone",
            "emaar", "omniyat", "sobha", "ellington",
        ]

        score = 0.45
        for w in high:
            if w in hl:
                score = 0.92
                break
        for w in mid:
            if w in hl:
                score = max(score, 0.70)

        if sentiment == "negative":
            score *= 0.4
        return score

    def render(self, inputs: TemplateInputs, output_dir: str = "apps/api/app/storage/renders") -> str:
        """DEPRECATED: Use render_to_bytes() instead."""
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "luxury_dark")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(self, inputs: TemplateInputs) -> Image.Image:
        W, H = self._get_dimensions()   # 1080 × 1350

        # ── Background ──────────────────────────────────────────────────
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
            bg = darken_image(bg, factor=0.52)
            bg = tint_image(bg, color=(0, 78, 65), strength=0.06)
        else:
            bg = Image.new("RGBA", (W, H), (10, 10, 14, 255))

        bg = apply_vignette(bg, strength=0.45, blur=80)

        # Gradient from bottom — darkens only the text zone
        bg = apply_diagonal_gradient(
            bg,
            color=(0, 0, 0),
            alpha_start=210,
            alpha_end=0,
            from_bottom=True,
            ratio=0.20,
        )

        draw = ImageDraw.Draw(bg)

        # Logo — top right corner
        logo_path = inputs.logo_path or self._find_logo()
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            logo = logo.resize((300, 300), Image.LANCZOS)
            bg.alpha_composite(logo, (10, -70))


        # Location tag
        if inputs.location_tag:
            draw_location_tag(bg, inputs.location_tag, position=(540, 930))

        # Gold horizontal divider 
        divider_y = 980
        draw.rectangle([(60, divider_y), (W - 60, divider_y + 2)], fill=BINAYAH_GOLD)

        # Headline
        draw_colored_headline(
            bg,
            headline=inputs.headline,
            box=(60, divider_y + 18, W - 60, 1300),
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=14,
            max_lines=4,
            start_font_size=56,
            line_spacing=10,
            highlight_color=BINAYAH_GOLD,
            shadow=True,
        )

        #  Gold bottom strip
        draw.rectangle([(0, H - 14), (W, H)], fill=BINAYAH_GOLD)

        # Website URL
        draw_url(bg, inputs.website_url, center=(540, H - 44), font_size=26,
                 color=(*WHITE[:3], 180))

        return bg

    @staticmethod
    def _find_logo() -> str | None:
        return resolve_logo_path("logo_w.png")