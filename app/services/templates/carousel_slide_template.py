"""
Carousel Slide Template — Binayah
4:5 format (1080 × 1350) for Instagram Carousel slides.
Renders one slide at a time with slide number indicator and optional slide caption.
"""
import os
from typing import Optional, Set
from PIL import Image, ImageDraw

from app.services.templates.base_template import (
    BaseTemplate, TemplateInputs,
    BINAYAH_TEAL, BINAYAH_GOLD, WHITE, BLACK,
)
from app.services.templates.rendering_helpers import (
    load_image_bytes, load_image_path, cover_resize, save_poster, image_to_bytes,
    apply_bottom_fade, apply_vignette,
    draw_colored_headline, draw_url,
    load_headline_font, load_body_font,
    resolve_logo_path,
)

SLIDE_W, SLIDE_H = 1080, 1350


class CarouselSlideTemplate(BaseTemplate):

    def get_name(self) -> str:
        return "carousel_slide"

    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        # Only selected explicitly — never auto-selected for feed posts
        return 0.0

    @staticmethod
    def _get_dimensions() -> tuple:
        return (SLIDE_W, SLIDE_H)

    def render(self, inputs: TemplateInputs, output_dir: str) -> str:
        bg = self._create_image(inputs)
        return save_poster(bg, output_dir, "carousel")

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        bg = self._create_image(inputs)
        return image_to_bytes(bg, format=format, quality=quality)

    def render_slide(
        self,
        inputs: TemplateInputs,
        slide_number: int,
        total_slides: int = 3,
        slide_caption: Optional[str] = None,
        format: str = "PNG",
        quality: int = 95,
    ) -> bytes:
        """
        Full carousel slide render with slide number pill and optional body caption.

        Args:
            inputs: Standard TemplateInputs (headline, background, gold_words etc.)
            slide_number: 1, 2, or 3
            total_slides: Total slides in the carousel (default 3)
            slide_caption: Short body text shown below headline (the angle text)
            format / quality: Output format
        """
        bg = self._create_image(inputs, slide_number, total_slides, slide_caption)
        return image_to_bytes(bg, format=format, quality=quality)

    def _create_image(
        self,
        inputs: TemplateInputs,
        slide_number: int = 1,
        total_slides: int = 3,
        slide_caption: Optional[str] = None,
    ) -> Image.Image:
        W, H = SLIDE_W, SLIDE_H

        # --- Background ---
        if inputs.background_image_bytes:
            bg = cover_resize(load_image_bytes(inputs.background_image_bytes), W, H)
        else:
            bg = Image.new("RGBA", (W, H), BINAYAH_TEAL)

        bg = apply_bottom_fade(bg, fade_ratio=0.72, color=(0, 0, 0))
        bg = apply_vignette(bg, strength=0.58, blur=80)

        draw = ImageDraw.Draw(bg)

        # --- Logo top-left — maintain aspect ratio, align gold bar below it ---
        logo_path = inputs.logo_path or self._find_logo()
        logo_bottom_y = 45  # fallback if no logo
        if logo_path and os.path.exists(logo_path):
            logo = load_image_path(logo_path)
            max_logo_w = 230
            lw, lh = logo.size
            scale = max_logo_w / lw
            logo = logo.resize((int(lw * scale), int(lh * scale)), Image.LANCZOS)
            logo_x, logo_y = 30, 28
            bg.alpha_composite(logo, (logo_x, logo_y))
            logo_bottom_y = logo_y + logo.size[1]

        # --- Slide number pill — top-right ---
        self._draw_slide_pill(draw, slide_number, total_slides, W)

        # --- Gold accent line cleanly below logo ---
        bar_y = logo_bottom_y + 8
        draw.rectangle([(30, bar_y), (30 + 200, bar_y + 4)], fill=BINAYAH_GOLD)

        # --- Headline zone — pushed lower ---
        if slide_caption:
            headline_box = (50, int(H * 0.58), W - 50, int(H * 0.75))
        else:
            headline_box = (50, int(H * 0.58), W - 50, int(H * 0.86))

        draw_colored_headline(
            bg,
            inputs.headline,
            box=headline_box,
            gold_words=inputs.gold_words,
            red_words=inputs.red_words,
            max_words=12,
            max_lines=4,
            start_font_size=74,
            line_spacing=8,
        )

        # --- Slide caption body text ---
        if slide_caption:
            self._draw_slide_caption(draw, slide_caption, W, H)

        # --- Website URL ---
        draw_url(bg, inputs.website_url, center=(W // 2, H - 48), font_size=28)

        # --- Bottom gold bar ---
        draw.rectangle([(0, H - 14), (W, H)], fill=BINAYAH_GOLD)

        # --- Swipe arrow hint on non-last slides ---
        if slide_number < total_slides:
            self._draw_swipe_hint(draw, W, H)

        return bg.convert("RGB")

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_slide_pill(draw: ImageDraw.ImageDraw, slide_num: int, total: int, W: int):
        """Draw '1 / 3' pill in top-right corner."""
        text = f"{slide_num}  /  {total}"
        font = load_body_font(26)
        bb = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]

        pad_x, pad_y = 20, 12
        rx = W - 30 - tw - pad_x * 2
        ry = 30
        rw = W - 30
        rh = ry + th + pad_y * 2

        draw.rounded_rectangle(
            [(rx, ry), (rw, rh)],
            radius=20,
            fill=(*BINAYAH_GOLD[:3], 200),
        )
        draw.text(
            (rx + pad_x, ry + pad_y),
            text,
            font=font,
            fill=BLACK,
        )

    @staticmethod
    def _draw_slide_caption(draw: ImageDraw.ImageDraw, caption: str, W: int, H: int):
        """Draw short body caption below headline."""
        font = load_body_font(32)
        max_w = W - 100
        words = caption.split()
        lines = []
        current = []

        for word in words:
            test = " ".join(current + [word])
            bb = draw.textbbox((0, 0), test, font=font)
            if bb[2] - bb[0] <= max_w:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
            if len(lines) >= 3:
                break
        if current and len(lines) < 3:
            lines.append(" ".join(current))

        # Draw with subtle semi-transparent backing
        line_h = font.size + 6
        total_h = len(lines) * line_h
        y_start = int(H * 0.78)

        for line in lines:
            bb = draw.textbbox((0, 0), line, font=font)
            lw = bb[2] - bb[0]
            x = (W - lw) // 2
            # Shadow
            draw.text((x + 2, y_start + 2), line, font=font, fill=(0, 0, 0, 160))
            draw.text((x, y_start), line, font=font, fill=(*WHITE[:3], 210))
            y_start += line_h

    @staticmethod
    def _draw_swipe_hint(draw: ImageDraw.ImageDraw, W: int, H: int):
        """Draw a subtle swipe-right arrow on non-last slides."""
        font = load_body_font(24)
        text = "swipe →"
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        x = W - tw - 36
        y = H - 80
        draw.text((x, y), text, font=font, fill=(*BINAYAH_GOLD[:3], 180))

    def _find_logo(self) -> str | None:
        return resolve_logo_path("logo_w.png")