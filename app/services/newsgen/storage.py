"""
IMAGE STORAGE — S3 (primary) + Local (optional backup)
"""

import os
import logging
import aiofiles
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger(__name__)

import boto3
from botocore.config import Config
from boto3.s3.transfer import TransferConfig
import io

BOTO_CONFIG = Config(
    connect_timeout=20,
    read_timeout=120,
    retries={"max_attempts": 10, "mode": "adaptive"},
)

TRANSFER = TransferConfig(
    multipart_threshold=5 * 1024 * 1024,
    multipart_chunksize=5 * 1024 * 1024,
    max_concurrency=8,
    use_threads=True,
)

s3 = boto3.client("s3", config=BOTO_CONFIG)

def put_bytes(bucket: str, key: str, data: bytes, content_type: str):
    bio = io.BytesIO(data)
    s3.upload_fileobj(
        bio,
        bucket,
        key,
        ExtraArgs={"ContentType": content_type},
        Config=TRANSFER,
    )



class LocalStorage:
    def __init__(self, base_path: str = None):
        self.base_path = base_path or settings.STORAGE_LOCAL_PATH
        Path(self.base_path).mkdir(parents=True, exist_ok=True)

    async def save(self, file_path: str, data: bytes) -> str:
        full_path = os.path.join(self.base_path, file_path)
        Path(os.path.dirname(full_path)).mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full_path, "wb") as f:
            await f.write(data)
        logger.info(f"  💾 Local: {full_path}")
        return f"/storage/{file_path}"

    async def read(self, file_path: str) -> bytes:
        async with aiofiles.open(os.path.join(self.base_path, file_path), "rb") as f:
            return await f.read()

    async def delete(self, file_path: str) -> bool:
        try:
            os.remove(os.path.join(self.base_path, file_path))
            return True
        except Exception:
            return False

    async def exists(self, file_path: str) -> bool:
        return os.path.exists(os.path.join(self.base_path, file_path))


class S3Storage:
    def __init__(self):
        import boto3
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION,
        )
        self.bucket = settings.AWS_S3_BUCKET
        self.region = settings.AWS_S3_REGION

    async def save(self, file_path: str, data: bytes) -> str:
        """Upload to S3 and return public HTTPS URL"""
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=file_path,
            Body=data,
            ContentType=self._get_content_type(file_path),
            ACL="public-read",  # 👈 Make object publicly readable
        )
        
        https_url = f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{file_path}"
        logger.info(f"  ☁️  S3 (public): {https_url}")
        return https_url

    async def read(self, file_path: str) -> bytes:
        resp = self.s3_client.get_object(Bucket=self.bucket, Key=file_path)
        return resp["Body"].read()

    async def delete(self, file_path: str) -> bool:
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=file_path)
            return True
        except Exception:
            return False

    async def exists(self, file_path: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=file_path)
            return True
        except Exception:
            return False

    def _get_content_type(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "application/octet-stream")
    
    def presign(self, file_path: str, expires: int = 3600) -> str:
        """Generate presigned URL for private files"""
        return self.s3_client.generate_presigned_url(
               "get_object",
                Params={"Bucket": self.bucket, "Key": file_path},
                ExpiresIn=expires,
        )
    
    def get_public_url(self, file_path: str) -> str:
        """Get the public HTTPS URL for an S3 object"""
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{file_path}"


class DualStorage:
    """S3 primary + Local backup. Returns S3 URL as canonical."""

    def __init__(self):
        self.s3 = S3Storage()
        self.keep_local = settings.STORAGE_KEEP_LOCAL
        self.local = LocalStorage() if self.keep_local else None

    async def save(self, file_path: str, data: bytes) -> str:
        s3_url = await self.s3.save(file_path, data)
        if self.local:
            try:
                await self.local.save(file_path, data)
            except Exception as e:
                logger.warning(f"  ⚠️ Local copy failed (non-fatal): {e}")
        return s3_url

    async def read(self, file_path: str) -> bytes:
        if self.local:
            try:
                return await self.local.read(file_path)
            except Exception:
                pass
        return await self.s3.read(file_path)

    async def delete(self, file_path: str) -> bool:
        s3_ok = await self.s3.delete(file_path)
        if self.local:
            await self.local.delete(file_path)
        return s3_ok

    async def exists(self, file_path: str) -> bool:
        return await self.s3.exists(file_path)


def get_storage():
    """Factory to get storage backend based on config."""
    backend = settings.STORAGE_BACKEND.lower()
    if backend == "dual":
        return DualStorage()
    elif backend == "s3":
        return S3Storage()
    return LocalStorage()