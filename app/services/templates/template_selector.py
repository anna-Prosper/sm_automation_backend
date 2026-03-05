"""
Template Selector — Binayah

Auto-selects the best template, or renders with a named template.
Provides a singleton and convenience functions used by the pipeline.
"""
from __future__ import annotations

import logging
from typing import Optional, List, Tuple

from app.services.templates.base_template import BaseTemplate, TemplateInputs
from app.services.templates.professional_luxury_template import ProfessionalLuxuryTemplate
from app.services.templates.bold_market_template import BoldMarketTemplate
from app.services.templates.elegant_minimal_template import ElegantMinimalTemplate
from app.services.templates.story_template import StoryTemplate
from app.services.templates.carousel_slide_template import CarouselSlideTemplate

logger = logging.getLogger(__name__)


class TemplateSelector:

    def __init__(self):
        self.templates: List[BaseTemplate] = [
            ProfessionalLuxuryTemplate(),
            BoldMarketTemplate(),
            ElegantMinimalTemplate(),
        ]
        self.template_map = {t.get_name(): t for t in self.templates}

        # Format-specific templates — selected explicitly, not auto-scored
        self.format_templates: dict = {
            "story": StoryTemplate(),
            "carousel_slide": CarouselSlideTemplate(),
        }
        self.template_map.update(self.format_templates)


    # Lookup

    def get_template_by_name(self, name: str) -> Optional[BaseTemplate]:
        return self.template_map.get(name)

    def get_all_template_names(self) -> List[str]:
        return list(self.template_map.keys())

    # Selection

    def select_best_template(
        self,
        headline: str,
        ai_suggestion: Optional[str] = None,
        sentiment: str = "neutral",
    ) -> BaseTemplate:
        """
        Pick the best template for this headline.
        1. Try ai_suggestion mapping first.
        2. Fall back to suitability scoring.
        """
        # 1. AI / explicit suggestion
        if ai_suggestion:
            suggestion_map = {
                "professional": "professional_luxury",
                "luxury": "professional_luxury",
                "bold": "bold_market",
                "market": "bold_market",
                "dramatic": "bold_market",
                "elegant": "elegant_minimal",
                "minimal": "elegant_minimal",
                "clean": "elegant_minimal",
            }
            name = suggestion_map.get(ai_suggestion.lower())
            if name and name in self.template_map:
                logger.info(f"📋 AI-suggested template: {name}")
                return self.template_map[name]

        # 2. Score all templates
        scores = []
        for t in self.templates:
            s = t.is_suitable_for(headline, sentiment)
            scores.append((t, s))
            logger.debug(f"   {t.get_name()}: {s:.2f}")

        best, best_score = max(scores, key=lambda x: x[1])
        logger.info(f"📋 Auto-selected template: {best.get_name()} (score {best_score:.2f})")
        return best

    def render_with_template(
        self,
        template_name: str,
        inputs: TemplateInputs,
        output_dir: str = "apps/api/app/storage/renders",
    ) -> str:
        """DEPRECATED: Use render_with_template_bytes() instead"""
        t = self.get_template_by_name(template_name)
        if not t:
            logger.warning(f"⚠️  Template '{template_name}' not found → fallback to professional_luxury")
            t = self.template_map["professional_luxury"]
        return t.render(inputs, output_dir)

    def auto_render(
        self,
        inputs: TemplateInputs,
        ai_suggestion: Optional[str] = None,
        sentiment: str = "neutral",
        output_dir: str = "apps/api/app/storage/renders",
    ) -> tuple[str, str]:
        """DEPRECATED: Use auto_render_bytes() instead. Select + render → (image_path, template_name)."""
        t = self.select_best_template(inputs.headline, ai_suggestion, sentiment)
        path = t.render(inputs, output_dir)
        return path, t.get_name()

    def render_with_template_bytes(
        self,
        template_name: str,
        inputs: TemplateInputs,
    ) -> bytes:
        """Render template and return image bytes (no file I/O)"""
        t = self.get_template_by_name(template_name)
        if not t:
            logger.warning(f"⚠️  Template '{template_name}' not found → fallback to professional_luxury")
            t = self.template_map["professional_luxury"]
        return t.render_to_bytes(inputs)

    def auto_render_bytes(
        self,
        inputs: TemplateInputs,
        ai_suggestion: Optional[str] = None,
        sentiment: str = "neutral",
    ) -> Tuple[bytes, str]:
        """Select best template + render to bytes → (image_bytes, template_name)"""
        t = self.select_best_template(inputs.headline, ai_suggestion, sentiment)
        image_bytes = t.render_to_bytes(inputs)
        return image_bytes, t.get_name()


_selector: TemplateSelector | None = None


def get_template_selector() -> TemplateSelector:
    global _selector
    if _selector is None:
        _selector = TemplateSelector()
    return _selector


def select_and_render(
    inputs: TemplateInputs,
    ai_suggestion: Optional[str] = None,
    sentiment: str = "neutral",
    output_dir: str = "apps/api/app/storage/renders",
) -> tuple[str, str]:
    """
    DEPRECATED: Use select_and_render_bytes() instead.
    
    Convenience: select best template + render to file.
    """
    return get_template_selector().auto_render(inputs, ai_suggestion, sentiment, output_dir)


def select_and_render_bytes(
    inputs: TemplateInputs,
    ai_suggestion: Optional[str] = None,
    sentiment: str = "neutral",
) -> Tuple[bytes, str]:
    """
    Convenience: select best template + render to bytes (S3-friendly).
    
    Usage::
        from app.services.templates import select_and_render_bytes, TemplateInputs
        
        image_bytes, template_name = select_and_render_bytes(
            inputs=TemplateInputs(headline="Emaar launches…", …),
            sentiment="positive",
        )
        
        # Upload to S3
        from app.services.newsgen.storage import get_storage
        storage = get_storage()
        url = await storage.save(f"images/post_{template_name}.png", image_bytes)
    """
    return get_template_selector().auto_render_bytes(inputs, ai_suggestion, sentiment)
