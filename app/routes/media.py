"""
Media Proxy Route
Fetches private S3 objects using server-side credentials and serves them publicly.
Used so Instagram (and other Meta platforms) can fetch images from a public URL
even when the S3 bucket is private.
"""

import logging
from urllib.parse import unquote

import boto3
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])


@router.get("/{key:path}")
async def proxy_s3_media(key: str):
    """
    Proxy an S3 object publicly using server-side AWS credentials.
    URL format: GET /api/media/images/poster_20260223_092427_2.png
    This allows Meta's crawler to fetch private S3 images via this public endpoint.
    """
    key = unquote(key)

    if not settings.AWS_S3_BUCKET:
        raise HTTPException(status_code=503, detail="S3 not configured")

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION,
        )

        obj = s3.get_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
        data = obj["Body"].read()
        content_type = obj.get("ContentType", "image/png")

        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
            },
        )

    except s3.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail=f"Media not found: {key}")
    except Exception as e:
        logger.error(f"Media proxy error for key={key}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch media")
