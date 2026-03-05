from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Literal

import requests


class StabilityError(RuntimeError):
    pass


@dataclass
class ImageResult:
    bytes: bytes
    provider: str
    ref: str


class StabilityImageProvider:
    """
    Stability AI Stable Image API.
    """

    def __init__(
        self,
        api_key: str,
        variant: Literal["core", "ultra", "sd3"] = "core",
        aspect_ratio: str = "1:1",
        output_format: Literal["png", "jpeg", "webp"] = "png",
        timeout_s: int = 120,
    ):
        self.api_key = api_key
        self.variant = variant
        self.aspect_ratio = aspect_ratio
        self.output_format = output_format
        self.timeout_s = timeout_s

    def generate(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> ImageResult:
        if not self.api_key:
            raise StabilityError("STABILITY_API_KEY missing")

        endpoint = f"https://api.stability.ai/v2beta/stable-image/generate/{self.variant}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "image/*",
        }

        # multipart fields
        files = {
            "prompt": (None, prompt),
            "aspect_ratio": (None, self.aspect_ratio),
            "output_format": (None, self.output_format),
        }

        if negative_prompt:
            files["negative_prompt"] = (None, negative_prompt)
        if seed is not None:
            files["seed"] = (None, str(seed))

        r = requests.post(endpoint, headers=headers, files=files, timeout=self.timeout_s)
        if r.status_code != 200:
            raise StabilityError(f"Stability failed {r.status_code}: {r.text}")

        return ImageResult(
            bytes=r.content,
            provider="stability",
            ref=f"{self.variant}|{self.aspect_ratio}|{self.output_format}",
        )

    def transform_image(
        self,
        image_bytes: bytes,
        prompt: str,
        negative_prompt: Optional[str] = None,
        strength: float = 0.7,
        seed: Optional[int] = None,
    ) -> ImageResult:
        """
        Transform an input image using image-to-image generation.
        
        Args:
            image_bytes: Input image bytes (article image)
            prompt: Transformation prompt (how to modify the image)
            negative_prompt: What to avoid in the output
            strength: Transformation strength (0.0-1.0, higher = more changes)
            seed: Random seed for reproducibility
            
        Returns:
            ImageResult with transformed image
        """
        if not self.api_key:
            raise StabilityError("STABILITY_API_KEY missing")

        # Use control endpoint for image-to-image
        endpoint = f"https://api.stability.ai/v2beta/stable-image/control/structure"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "image/*",
        }

        # multipart fields
        files = {
            "image": ("input.png", image_bytes, "image/png"),
            "prompt": (None, prompt),
            "control_strength": (None, str(strength)),
            "output_format": (None, self.output_format),
        }

        if negative_prompt:
            files["negative_prompt"] = (None, negative_prompt)
        if seed is not None:
            files["seed"] = (None, str(seed))

        r = requests.post(endpoint, headers=headers, files=files, timeout=self.timeout_s)
        if r.status_code != 200:
            raise StabilityError(f"Stability image-to-image failed {r.status_code}: {r.text}")

        return ImageResult(
            bytes=r.content,
            provider="stability_i2i",
            ref=f"i2i|{self.variant}|strength:{strength}|{self.output_format}",
        )


def get_stability_provider_from_env() -> StabilityImageProvider:
    api_key = os.getenv("STABILITY_API_KEY").strip()
    variant = os.getenv("STABILITY_VARIANT", "core").strip().lower()
    aspect_ratio = os.getenv("STABILITY_ASPECT_RATIO", "4:5").strip()
    output_format = os.getenv("STABILITY_OUTPUT_FORMAT", "png").strip().lower()
    timeout_s = int(os.getenv("STABILITY_TIMEOUT_S", "120"))

    if variant not in ("core", "ultra", "sd3"):
        variant = "core"
    if output_format not in ("png", "jpeg", "webp"):
        output_format = "png"

    return StabilityImageProvider(
        api_key=api_key,
        variant=variant,
        aspect_ratio=aspect_ratio,
        output_format=output_format,
        timeout_s=timeout_s,
    )