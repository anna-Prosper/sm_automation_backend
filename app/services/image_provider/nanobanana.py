"""
NanoBanana AI Image Provider
"""
from __future__ import annotations

import io
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Literal

import requests

logger = logging.getLogger(__name__)


class NanoBananaError(RuntimeError):
    pass


@dataclass
class ImageResult:
    bytes: bytes
    provider: str
    ref: str


class NanoBananaImageProvider:
    """
    NanoBanana AI image generation API.
    Provides generate() and transform_image() with the same interface
    """

    BASE_URL = "https://api.nanobananaapi.ai/api/v1/nanobanana"

    ASPECT_MAP = {
        "1:1": "1:1",
        "4:5": "4:5",
        "9:16": "9:16",
        "16:9": "16:9",
        "3:2": "3:2",
        "2:3": "2:3",
    }

    def __init__(
        self,
        api_key: str,
        aspect_ratio: str = "1:1",
        output_format: Literal["png", "jpeg"] = "png",
        poll_interval: float = 3.0,
        timeout_s: int = 240,
        download_retries: int = 4,
    ):
        self.api_key = api_key
        self.aspect_ratio = aspect_ratio
        self.output_format = output_format
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s
        self.download_retries = download_retries

    # public API

    def generate(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> ImageResult:
        """Text-to-image generation."""
        if not self.api_key:
            raise NanoBananaError("NANOBANANA_API_KEY missing")

        image_size = self.ASPECT_MAP.get(self.aspect_ratio, "1:1")

        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}. Avoid: {negative_prompt}"

        payload = {
            "prompt": full_prompt,
            "numImages": 1,
            "type": "TEXTTOIAMGE",
            "image_size": image_size,
            "callBackUrl": "https://example.com/webhook",
        }

        task_id = self._create_task(payload)
        result_url = self._poll_result(task_id)
        image_bytes = self._download_result(result_url)
        print("Generate")
        return ImageResult(
            bytes=image_bytes,
            provider="nanobanana",
            ref=f"t2i|{image_size}",
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
        Image-to-image transformation.
        NanoBanana requires a URL for the input image, so we upload
        the bytes to S3 and generate a presigned URL first.
        """
        if not self.api_key:
            raise NanoBananaError("NANOBANANA_API_KEY missing")

        # Upload input image to S3 → presigned URL
        input_url = self._upload_temp_image_to_s3(image_bytes)
        logger.info(f"   [NanoBanana] Input image uploaded for i2i")

        image_size = self.ASPECT_MAP.get(self.aspect_ratio, "1:1")

        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}. Avoid: {negative_prompt}"

        payload = {
            "prompt": full_prompt,
            "numImages": 1,
            "type": "IMAGETOIAMGE",
            "image_size": image_size,
            "imageUrls": [input_url],
            "callBackUrl": "https://example.com/webhook",
        }

        task_id = self._create_task(payload)
        result_url = self._poll_result(task_id)
        image_bytes = self._download_result(result_url)
        print("Transform")
        
        return ImageResult(
            bytes=image_bytes,
            provider="nanobanana_i2i",
            ref=f"i2i|{image_size}|strength:{strength}",
        )

    # internal helpers

    def _create_task(self, payload: dict) -> str:
        """POST /generate → returns taskId."""
        url = f"{self.BASE_URL}/generate"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()

        if data.get("code") != 200:
            raise NanoBananaError(f"NanoBanana create task failed: {data}")

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            raise NanoBananaError(f"NanoBanana no taskId in response: {data}")

        logger.info(f"   [NanoBanana] Task created: {task_id}")
        return task_id

    def _poll_result(self, task_id: str) -> str:
        """GET /record-info until successFlag==1 → returns resultImageUrl."""
        url = f"{self.BASE_URL}/record-info"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        start = time.time()
        while True:
            r = requests.get(
                url, headers=headers, params={"taskId": task_id}, timeout=60
            )
            r.raise_for_status()
            data = r.json()

            if data.get("code") != 200:
                raise NanoBananaError(f"NanoBanana poll error: {data}")

            info = data.get("data", {})
            flag = info.get("successFlag")

            if flag == 1:
                resp = info.get("response") or {}
                out_url = resp.get("resultImageUrl")
                if not out_url:
                    raise NanoBananaError(
                        f"NanoBanana success but no resultImageUrl: {data}"
                    )
                logger.info(f"   [NanoBanana] Generation complete")
                return out_url

            if flag in (2, 3):
                raise NanoBananaError(
                    f"NanoBanana task failed: "
                    f"errorCode={info.get('errorCode')} "
                    f"msg={info.get('errorMessage')}"
                )

            elapsed = time.time() - start
            if elapsed > self.timeout_s:
                raise NanoBananaError(
                    f"NanoBanana timeout after {int(elapsed)}s for task {task_id}"
                )

            time.sleep(self.poll_interval)

    def _download_result(self, url: str) -> bytes:
        """Download generated image from NanoBanana's CDN.
        
        NanoBanana marks a task as complete before its CDN (tempfile.aiquickdraw.com)
        has finished writing the file. A short initial wait avoids hitting 524
        Cloudflare origin-timeout errors on the first attempt.
        """
        headers = {"User-Agent": "Mozilla/5.0"}
        last_err = None

        # Give the CDN a moment to make the file available after generation completes
        time.sleep(20)

        for attempt in range(1, self.download_retries + 1):
            try:
                r = requests.get(url, headers=headers, timeout=(15, 180))
                r.raise_for_status()
                if not r.content:
                    raise NanoBananaError("Empty response body")
                return r.content
            except requests.RequestException as e:
                last_err = e
                wait = min(5 * attempt, 30)   # 5s, 10s, 15s, 20s  (was 2^n: 2s, 4s, 8s, 16s)
                logger.warning(
                    f"   [NanoBanana] Download attempt {attempt}/{self.download_retries} failed: {e}"
                    f" — retrying in {wait}s"
                )
                time.sleep(wait)

        raise NanoBananaError(
            f"Failed to download NanoBanana result after {self.download_retries} retries: {last_err}"
        )

    @staticmethod
    def _upload_temp_image_to_s3(image_bytes: bytes, expires: int = 3600) -> str:
        """
        Upload image bytes to a temp S3 path and return a presigned GET URL.
        Used for img2img where NanoBanana needs a URL, not raw bytes.
        """
        import boto3
        from app.core.config import settings

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION,
        )

        key = f"nanobanana-inputs/{uuid.uuid4().hex}.png"

        s3.put_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=key,
            Body=image_bytes,
            ContentType="image/png",
        )

        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )
        return presigned_url


def get_nanobanana_provider_from_env() -> NanoBananaImageProvider:
    """Factory: create provider from environment variables."""
    api_key = (os.getenv("NANOBANANA_API_KEY") or "").strip()
    aspect_ratio = (os.getenv("NANOBANANA_ASPECT_RATIO", "4:5")).strip()
    timeout_s = int(os.getenv("NANOBANANA_TIMEOUT_S", "240"))

    return NanoBananaImageProvider(
        api_key=api_key,
        aspect_ratio=aspect_ratio,
        timeout_s=timeout_s,
    )