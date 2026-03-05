"""
Story Template — Binayah
9:16 vertical format (1080 × 1920) for Instagram/WhatsApp Stories.
"""
import os
from typing import Set
from PIL import Image, ImageDraw

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    apply_bottom_fade, apply_vignette, darken_image,
    draw_colored_headline, draw_url, draw_bottom_logo,
    load_headline_font, load_body_font,
    resolve_logo_path,
)

# Story canvas: 9:16
STORY_W, STORY_H = 1080, 1920


class StoryTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "story"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        # Only selected explicitly — never auto-selected for feed posts
        return 0.0

    @staticmethod
    def _get_dimensions() -> tuple:
        return (STORY_W, STORY_H)

    def render(self, inputs: TemplateInputs, output_dir: str) -> str:
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "story")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(self, inputs: TemplateInputs) -> Image.Image:
        W, H = STORY_W, STORY_H

        # --- Background ---
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
        else:
            bg = Image.new("RGBA", (W, H), BINAYAH_TEAL)

        # Stronger fade for stories — bottom 65% darkens heavily
        bg = apply_bottom_fade(bg, fade_ratio=0.65, color=(0, 0, 0))
        # Lighter top fade for logo readability
        bg = self._apply_top_fade(bg, fade_ratio=0.25)
        bg = apply_vignette(bg, strength=0.55, blur=100)

        # --- Logo top-left — maintain aspect ratio, align gold bar below it ---
        logo_path = inputs.logo_path or self._find_logo()
        logo_bottom_y = 50  # fallback if no logo
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            max_logo_w = 260
            lw, lh = logo.size
            scale = max_logo_w / lw
            logo = logo.resize((int(lw * scale), int(lh * scale)), Image.LANCZOS)
            logo_x, logo_y = 35, 35
            bg.alpha_composite(logo, (logo_x, logo_y))
            logo_bottom_y = logo_y + logo.size[1]

        # --- Gold accent bar cleanly below logo ---
        draw = ImageDraw.Draw(bg)
        bar_y = logo_bottom_y + 10
        draw.rectangle([(35, bar_y), (35 + 220, bar_y + 4)], fill=BINAYAH_GOLD)

        # --- Headline — lower third of frame ---
        headline_box = (60, int(H * 0.60), W - 60, int(H * 0.88))
        draw_colored_headline(
            bg,
            inputs.headline,
            box=headline_box,
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=14,
            max_lines=5,
            start_font_size=90,
            line_spacing=10,
        )

        # --- Website URL bottom-center ---
        draw_url(bg, inputs.website_url, center=(W // 2, H - 70), font_size=36)

        # --- Thin gold line above URL ---
        draw.rectangle([(W // 2 - 120, H - 100), (W // 2 + 120, H - 96)], fill=BINAYAH_GOLD)

        # --- Swipe-up hint (optional style element) ---
        font = load_body_font(28)
        hint = "↑  LEARN MORE"
        hint_bb = draw.textbbox((0, 0), hint, font=font)
        hint_w = hint_bb[2] - hint_bb[0]
        draw.text(((W - hint_w) // 2, H - 140), hint, font=font, fill=(*WHITE[:3], 160))

        return bg.convert("RGB")

    @staticmethod
    def _apply_top_fade(img: Image.Image, fade_ratio: float = 0.25) -> Image.Image:
        """Subtle dark top fade so logo stays readable over bright backgrounds."""
        from PIL import Image as PILImage
        w, h = img.size
        fade_h = int(h * fade_ratio)
        overlay = PILImage.new("RGBA", (w, fade_h), (0, 0, 0, 0))
        for y in range(fade_h):
            alpha = int(160 * (1 - y / fade_h))  # dark at top, transparent downward
            for x in range(w):
                overlay.putpixel((x, y), (0, 0, 0, alpha))
        out = img.copy()
        out.paste(overlay, (0, 0), overlay)
        return out

    def _find_logo(self) -> str | None:
        return resolve_logo_path("logo_w.png")