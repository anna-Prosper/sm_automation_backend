"""
Base Template Interface — Binayah Real Estate
All poster templates inherit from this base class.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Set
from PIL import Image
from io import BytesIO


# Binayah brand palette 
BINAYAH_TEAL  = (0, 78, 65, 255)      # #004e41
BINAYAH_GOLD  = (209, 174, 74, 255)   # #d1ae4a
WHITE         = (255, 255, 255, 255)
BLACK         = (0, 0, 0, 255)
RED           = (220, 50, 50, 255)


@dataclass
class TemplateInputs:
    """Common inputs for all templates."""
    headline: str
    website_url: str = "binayah.com"
    gold_words: Set[str] = field(default_factory=set) 
    background_image_bytes: Optional[bytes] = None
    logo_path: Optional[str] = None
    red_words: Optional[Set[str]] = None
    location_tag: Optional[str] = None
    developer_tag: Optional[str] = None


class BaseTemplate(ABC):
    """Abstract base for every Binayah poster template."""

    @abstractmethod
    def render(self, inputs: TemplateInputs, output_dir: str) -> str:
        """DEPRECATED: Use render_to_bytes() instead. Render poster → save to file → return filepath."""
        ...

    @abstractmethod
    def get_name(self) -> str:
        """Template identifier, e.g. 'professional_luxury'."""
        ...

    @abstractmethod
    def is_suitable_for(self, headline: str, sentiment: str = "neutral") -> float:
        """Suitability score 0.0–1.0 for this headline."""
        ...

    def render_to_bytes(self, inputs: TemplateInputs, format: str = "PNG", quality: int = 95) -> bytes:
        """
        Render poster directly to bytes (no file I/O) for S3 upload.
        
        Args:
            inputs: Template inputs
            format: Image format (PNG, JPEG, etc.)
            quality: Quality for JPEG (1-95)
            
        Returns:
            bytes: Image data ready for S3 upload
        """
        import tempfile
        import os
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.render(inputs, tmpdir)
            with open(path, "rb") as f:
                return f.read()

    @staticmethod
    def _get_dimensions() -> tuple:
        return (1080, 1350)

    @staticmethod
    def extract_gold_words(headline: str) -> Set[str]:
        """
        Extract real-estate keywords to highlight in gold.
        """
        keywords = {
            # Developers
            "EMAAR", "DAMAC", "NAKHEEL", "MERAAS", "SOBHA",
            "OMNIYAT", "ALDAR", "AZIZI", "DEYAAR", "DANUBE",
            "BINGHATTI", "ELLINGTON", "REPORTAGE",
            # Locations
            "DUBAI", "ABU", "DHABI", "SHARJAH",
            "MARINA", "JUMEIRAH", "PALM", "DOWNTOWN",
            "CREEK", "HARBOUR", "HILLS", "SPRINGS",
            "JBR", "JLT", "DIFC", "JVC",
            # Property types
            "VILLA", "VILLAS", "PENTHOUSE", "PENTHOUSES",
            "APARTMENT", "APARTMENTS", "TOWNHOUSE", "TOWNHOUSES",
            "MANSION", "RESIDENCE", "RESIDENCES",
            # Financial / Market
            "AED", "BILLION", "MILLION", "RECORD",
            "ROI", "YIELD", "INVESTMENT",
            # Action words
            "LAUNCH", "LAUNCHED", "LAUNCHES",
            "SOLD", "SELLS", "SALE",
            "RECORD", "BREAKING",
        }
        tokens = headline.upper().split()
        cleaned = {t.strip("()[]{}.,:;!?'\"–—") for t in tokens}
        return cleaned & keywords

    @staticmethod
    def extract_red_words(headline: str) -> Set[str]:
        """Extract negative/urgent keywords."""
        red_keys = {
            "CRASH", "DROP", "FALL", "DECLINE", "LOSS",
            "WARNING", "RISK", "FRAUD", "SCAM", "BUBBLE",
            "DELAY", "DELAYED", "STALL", "STALLED",
            "OVERPRICED", "SLUMP",
        }
        tokens = headline.upper().split()
        cleaned = {t.strip("()[]{}.,:;!?'\"–—") for t in tokens}
        return cleaned & red_keys
