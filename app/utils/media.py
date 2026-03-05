from urllib.parse import urlparse
from app.core.config import settings


def _extract_bucket_key(value: str):
    if not value:
        return None, None

    if value.startswith("http"):
        u = urlparse(value)
        host = u.netloc or ""
        key = u.path.lstrip("/")

        if "s3" in host and "amazonaws.com" in host and key:
            bucket = host.split(".")[0]
            return bucket, key

    return None, None


def resolve_media_url(value: str, expires_in: int = 3600) -> str:
    """
    Convert S3 HTTPS URLs to presigned URLs for private buckets.
    
    For private S3 buckets, we need presigned URLs that give temporary access.
    """
    if not value:
        return value

    if "AWSAccessKeyId" in value or "X-Amz-Signature" in value:
        return value

    bucket, key = _extract_bucket_key(value)
    if not bucket or not key:
        return value

    try:
        import boto3
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION,
            config=boto3.session.Config(signature_version='s3v4')
        )
        
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return presigned_url
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return value