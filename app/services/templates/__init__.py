"""Binayah post template system."""
from app.services.templates.base_template import BaseTemplate, TemplateInputs
from app.services.templates.template_selector import (
    TemplateSelector,
    get_template_selector,
    select_and_render,
)

__all__ = [
    "BaseTemplate",
    "TemplateInputs",
    "TemplateSelector",
    "get_template_selector",
    "select_and_render",
]
