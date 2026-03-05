"""
Social Media Publisher Service
Handles posting to WhatsApp (WABA), Facebook Pages, and Instagram
via the Meta Graph API.

FIXES INCLUDED:
- Instagram rejects WEBP/AVIF → auto-convert to JPEG, upload to S3, use presigned URL
- Meta Graph endpoints work best with form params (data=...) not json=
- Poll IG container status_code until FINISHED (sleep(5) is not reliable)
- Fix WhatsApp function returning wrong variable
"""

import asyncio
import logging
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

META_GRAPH_BASE = "https://graph.facebook.com/v19.0"

# IG publishing typically supports JPEG/PNG (NOT WEBP/AVIF)
IG_ALLOWED_EXTS = {".jpg", ".jpeg", ".png"}
IG_BAD_EXTS = {".webp", ".avif"}


class SocialPublisher:
    """
    Unified publisher for Meta platforms (WhatsApp, Facebook, Instagram).
    """

    def __init__(self):
        self.access_token = settings.META_ACCESS_TOKEN
        self.whatsapp_phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.facebook_page_id = settings.FACEBOOK_PAGE_ID
        self.instagram_account_id = settings.INSTAGRAM_ACCOUNT_ID
        self.whatsapp_default_recipients = settings.WHATSAPP_DEFAULT_RECIPIENTS

        self.aws_key = settings.AWS_ACCESS_KEY_ID
        self.aws_secret = settings.AWS_SECRET_ACCESS_KEY
        self.aws_bucket = settings.AWS_S3_BUCKET
        self.aws_region = settings.AWS_S3_REGION

    # Headers/Auth
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _auth_params(self) -> Dict[str, str]:
        return {"access_token": self.access_token}

    async def publish_whatsapp(
        self,
        caption: str,
        image_url: Optional[str] = None,
        recipients: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Send a WhatsApp message via the WANotifier Messaging API.

        WANotifier wraps the official WhatsApp Cloud API and handles
        template compliance, conversation windows, and delivery.

        Requires in .env:
            WANOTIFIER_API_KEY
            WHATSAPP_DEFAULT_RECIPIENTS

        Phone numbers must include country code with + prefix.
        """
        api_key = settings.WANOTIFIER_API_KEY
        if not api_key:
            return {
                "success": False,
                "error": (
                    "WANOTIFIER_API_KEY not set."
                ),
            }

        targets = recipients or self.whatsapp_default_recipients
        if not targets:
            return {"success": False, "error": "No WhatsApp recipients configured (WHATSAPP_DEFAULT_RECIPIENTS)"}

        url = "https://app.wanotifier.com/api/v1/messages"
        results = {"success": True, "message_ids": [], "errors": []}

        async with httpx.AsyncClient(timeout=30) as client:
            for phone in targets:
                phone_clean = phone.strip()
                if not phone_clean.startswith("+"):
                    phone_clean = "+" + phone_clean

                # Build message payload per WANotifier API spec
                if image_url:
                    message_payload = {
                        "type": "image",
                        "image": {
                            "link": image_url,
                            "caption": caption[:1024],
                        },
                    }
                else:
                    message_payload = {
                        "type": "text",
                        "text": {
                            "body": caption[:4096],
                        },
                    }

                payload = {
                    "recipient": {"whatsapp_number": phone_clean},
                    "message": message_payload,
                }

                try:
                    resp = await client.post(
                        url,
                        params={"key": api_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    data = resp.json()

                    if resp.status_code in (200, 201):
                        msg_id = data.get("message_id") or data.get("id", "")
                        results["message_ids"].append({"phone": phone_clean, "id": msg_id})
                        logger.info(f"WANotifier WhatsApp sent to {phone_clean}: {msg_id}")
                    else:
                        err = data.get("message") or data.get("error") or resp.text
                        results["errors"].append({"phone": phone_clean, "error": err})
                        logger.error(f"WANotifier WhatsApp failed for {phone_clean}: {err}")

                except Exception as e:
                    results["errors"].append({"phone": phone_clean, "error": str(e)})
                    logger.exception(f"WANotifier WhatsApp exception for {phone_clean}")

        if results["errors"]:
            results["success"] = len(results["message_ids"]) > 0

        return results


    async def publish_facebook(
        self,
        caption: str,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Publish a post to a Facebook Page.
        If image_url is provided, creates a photo post; otherwise a text post.
        """
        if not self.facebook_page_id:
            return {"success": False, "error": "FACEBOOK_PAGE_ID not configured"}

        async with httpx.AsyncClient(timeout=60) as client:
            try:
                if image_url:
                    url = f"{META_GRAPH_BASE}/{self.facebook_page_id}/photos"
                    payload = {"url": image_url, "caption": caption}
                else:
                    url = f"{META_GRAPH_BASE}/{self.facebook_page_id}/feed"
                    payload = {"message": caption}

                resp = await client.post(
                    url,
                    params=self._auth_params(),
                    data=payload,
                    headers=self._headers(),
                )
                data = resp.json()

                if resp.status_code in (200, 201):
                    post_id = data.get("id") or data.get("post_id", "")
                    logger.info(f"Facebook post created: {post_id}")
                    return {"success": True, "platform_post_id": post_id}

                err = data.get("error", {}).get("message", resp.text)
                logger.error(f"Facebook publish failed: {err}")
                return {"success": False, "error": err, "raw": data}

            except Exception as e:
                logger.exception("Facebook publish exception")
                return {"success": False, "error": str(e)}


    async def publish_facebook_story(
        self,
        image_url: str,
        caption: str = "",
    ) -> Dict[str, Any]:
        """
        Publish a photo Story to a Facebook Page.
        Uses the /page_id/photo_stories endpoint.
        """
        if not self.facebook_page_id:
            return {"success": False, "error": "FACEBOOK_PAGE_ID not configured"}

        async with httpx.AsyncClient(timeout=60) as client:
            try:
                # Upload photo as unpublished first
                upload_url = f"{META_GRAPH_BASE}/{self.facebook_page_id}/photos"
                upload_payload = {"url": image_url, "published": "false"}
                if caption:
                    upload_payload["caption"] = caption

                upload_resp = await client.post(
                    upload_url,
                    params=self._auth_params(),
                    data=upload_payload,
                    headers=self._headers(),
                )
                upload_data = upload_resp.json()
                photo_id = upload_data.get("id")

                if not photo_id:
                    err = upload_data.get("error", {}).get("message", "No photo ID returned")
                    logger.error(f"Facebook Story photo upload failed: {err}")
                    return {"success": False, "error": err}

                # Create the story
                story_url = f"{META_GRAPH_BASE}/{self.facebook_page_id}/photo_stories"
                story_payload = {"photo_id": photo_id}

                resp = await client.post(
                    story_url,
                    params=self._auth_params(),
                    data=story_payload,
                    headers=self._headers(),
                )
                data = resp.json()

                if resp.status_code in (200, 201):
                    story_id = data.get("id") or data.get("post_id", "")
                    logger.info(f"Facebook Story created: {story_id}")
                    return {"success": True, "platform_post_id": story_id}

                err = data.get("error", {}).get("message", resp.text)
                logger.error(f"Facebook Story publish failed: {err}")
                return {"success": False, "error": err, "raw": data}

            except Exception as e:
                logger.exception("Facebook Story publish exception")
                return {"success": False, "error": str(e)}

    async def _wait_ig_container(
        self,
        client: httpx.AsyncClient,
        container_id: str,
        timeout_s: int = 180,
    ) -> None:
        """
        Poll container status until FINISHED or ERROR.
        """
        deadline = time.monotonic() + timeout_s
        last = None

        while time.monotonic() < deadline:
            r = await client.get(
                f"{META_GRAPH_BASE}/{container_id}",
                params={**self._auth_params(), "fields": "status_code"},
                headers=self._headers(),
            )
            data = r.json()
            last = data.get("status_code")

            if last == "FINISHED":
                return
            if last in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"IG container status={last} response={data}")

            await asyncio.sleep(3)

        raise TimeoutError(f"IG container not ready in {timeout_s}s. Last status={last}")

    def _url_ext(self, url: str) -> str:
        try:
            p = urlparse(url)
            path = p.path.lower()
            if "." in path:
                return "." + path.split(".")[-1]
        except Exception:
            pass
        return ""

    async def _download_bytes(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

    def _convert_to_jpeg(self, raw: bytes) -> bytes:
        """
        Convert any image bytes to JPEG bytes.
        """
        im = Image.open(BytesIO(raw))
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        elif im.mode != "RGB":
            im = im.convert("RGB")

        out = BytesIO()
        im.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue()

    async def _upload_jpeg_to_s3_and_presign(self, jpeg_bytes: bytes) -> str:
        """
        Upload converted JPEG to S3 and return a long-expiry presigned URL.
        """
        if not (self.aws_bucket and self.aws_key and self.aws_secret):
            raise RuntimeError("AWS S3 credentials/bucket not configured for IG conversion upload")

        import boto3

        s3 = boto3.client(
            "s3",
            aws_access_key_id=self.aws_key,
            aws_secret_access_key=self.aws_secret,
            region_name=self.aws_region,
        )

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        key = f"ig/converted/{ts}.jpg"

        s3.put_object(
            Bucket=self.aws_bucket,
            Key=key,
            Body=jpeg_bytes,
            ContentType="image/jpeg",
        )


        public_url = f"https://{self.aws_bucket}.s3.{self.aws_region}.amazonaws.com/{key}"
        return public_url

    def _s3_url_to_proxy(self, image_url: str) -> str:
        """
        Convert an S3 URL to a proxy URL served by this API.
        e.g. https://bucket.s3.region.amazonaws.com/images/foo.png
          -> https://api.yourdomain.com/api/media/images/foo.png
        Only converts if API_BASE_URL is configured.
        """
        api_base = settings.API_BASE_URL.rstrip("/")
        if not api_base:
            return image_url

        from urllib.parse import urlparse
        parsed = urlparse(image_url)
        if "amazonaws.com" not in parsed.netloc:
            return image_url

        key = parsed.path.lstrip("/")
        proxy_url = f"{api_base}/api/media/{key}"
        logger.info(f"S3 URL proxied for IG: {image_url} -> {proxy_url}")
        return proxy_url

    async def _ensure_ig_compatible_url(self, image_url: str) -> str:
        """
        Make sure image_url is acceptable to Instagram container:
        - Convert S3 URLs to proxy URLs (so Meta can fetch from our public API)
        - If extension is .jpg/.jpeg/.png → use proxy URL as-is
        - If .webp/.avif/unknown → download, convert to JPEG, re-upload, proxy
        """
        if not image_url:
            return image_url

        # Always convert S3 URLs to proxy URLs first
        image_url = self._s3_url_to_proxy(image_url)

        ext = self._url_ext(image_url)

        if ext in IG_ALLOWED_EXTS:
            return image_url

        try:
            raw = await self._download_bytes(image_url)
            jpeg = self._convert_to_jpeg(raw)
            new_url = await self._upload_jpeg_to_s3_and_presign(jpeg)

            new_url = self._s3_url_to_proxy(new_url)
            logger.info(f"IG media converted ({ext or 'unknown'}) → JPEG proxied")
            return new_url
        except Exception as e:
            logger.error(f"IG media conversion failed for url={image_url}: {e}")
            return image_url


    async def publish_instagram(self, caption: str, image_url: str) -> Dict[str, Any]:
        """
        Publish a single-image post to Instagram Business/Creator account.

        Fix: Convert WEBP/AVIF to JPEG before calling IG container.
        """
        if not self.instagram_account_id:
            return {"success": False, "error": "INSTAGRAM_ACCOUNT_ID not configured"}

        if not image_url:
            return {"success": False, "error": "Instagram requires an image URL"}

        ig_url = await self._ensure_ig_compatible_url(image_url)


        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as probe:
                head = await probe.head(ig_url)
                if head.status_code != 200:
                    logger.error(f"IG image URL returned {head.status_code} — likely not public: {ig_url}")
                    return {"success": False, "error": f"Image URL not publicly accessible (HTTP {head.status_code}). Check S3 Block Public Access settings."}
                content_type = head.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    logger.error(f"IG image URL has wrong content-type '{content_type}': {ig_url}")
                    return {"success": False, "error": f"Image URL has wrong content-type '{content_type}'. Must be image/jpeg or image/png."}
                logger.info(f"IG image URL probe OK: {head.status_code} {content_type}")
        except Exception as probe_err:
            logger.error(f"IG image URL probe failed: {probe_err}")
            return {"success": False, "error": f"Image URL not reachable: {probe_err}"}
        async with httpx.AsyncClient(timeout=180) as client:
            try:
                # Step 1: Create container (FORM)
                container_url = f"{META_GRAPH_BASE}/{self.instagram_account_id}/media"
                print(container_url)
                resp1 = await client.post(
                    container_url,
                    params=self._auth_params(),
                    data={"image_url": ig_url, "caption": caption},
                    headers=self._headers(),
                )
                data1 = resp1.json()
                print("Data1:", data1)

                if resp1.status_code not in (200, 201) or "id" not in data1:
                    err = data1.get("error", {}).get("message", resp1.text)
                    logger.error(f"IG container creation failed: {err}")
                    return {"success": False, "error": f"Container failed: {err}", "raw": data1}

                container_id = data1["id"]
                print("Id:", container_id)
                logger.info(f"IG container created: {container_id}")

                # Step 1b: Wait until FINISHED
                await self._wait_ig_container(client, container_id, timeout_s=240)

                # Step 2: Publish (FORM)
                publish_url = f"{META_GRAPH_BASE}/{self.instagram_account_id}/media_publish"
                resp2 = await client.post(
                    publish_url,
                    params=self._auth_params(),
                    data={"creation_id": container_id},
                    headers=self._headers(),
                )
                data2 = resp2.json()

                if resp2.status_code in (200, 201) and "id" in data2:
                    post_id = data2["id"]
                    logger.info(f"Instagram post published: {post_id}")
                    return {"success": True, "platform_post_id": post_id}

                err = data2.get("error", {}).get("message", resp2.text)
                logger.error(f"IG publish failed: {err}")
                return {"success": False, "error": f"Publish failed: {err}", "raw": data2}

            except Exception as e:
                logger.exception("Instagram publish exception")
                return {"success": False, "error": str(e)}


    async def publish_instagram_story(self, image_url: str) -> Dict[str, Any]:
        """
        Publish an image as an Instagram Story.

        Key difference from a feed post:
          - media_type=STORIES must be set in the container creation request
          - Captions are NOT supported on stories via the API (Meta ignores/rejects them)
          - Same two-step flow: create container → media_publish
        """
        if not self.instagram_account_id:
            return {"success": False, "error": "INSTAGRAM_ACCOUNT_ID not configured"}

        if not image_url:
            return {"success": False, "error": "Instagram Story requires an image URL"}

        ig_url = await self._ensure_ig_compatible_url(image_url)

        # Probe URL accessibility
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as probe:
                head = await probe.head(ig_url)
                if head.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Story image URL not publicly accessible (HTTP {head.status_code}). Check S3 Block Public Access settings.",
                    }
                content_type = head.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    return {
                        "success": False,
                        "error": f"Story image URL has wrong content-type '{content_type}'. Must be image/jpeg or image/png.",
                    }
                logger.info(f"IG story image probe OK: {head.status_code} {content_type}")
        except Exception as probe_err:
            logger.error(f"IG story image probe failed: {probe_err}")
            return {"success": False, "error": f"Story image URL not reachable: {probe_err}"}

        async with httpx.AsyncClient(timeout=180) as client:
            try:
                # Step 1: Create STORY container
                container_url = f"{META_GRAPH_BASE}/{self.instagram_account_id}/media"
                resp1 = await client.post(
                    container_url,
                    params=self._auth_params(),
                    data={
                        "image_url": ig_url,
                        "media_type": "STORIES",
                    },
                    headers=self._headers(),
                )
                data1 = resp1.json()

                if resp1.status_code not in (200, 201) or "id" not in data1:
                    err = data1.get("error", {}).get("message", resp1.text)
                    logger.error(f"IG story container creation failed: {err}")
                    return {"success": False, "error": f"Story container failed: {err}", "raw": data1}

                container_id = data1["id"]
                logger.info(f"IG story container created: {container_id}")

                # Step 1b: Wait until FINISHED
                await self._wait_ig_container(client, container_id, timeout_s=240)

                # Step 2: Publish
                publish_url = f"{META_GRAPH_BASE}/{self.instagram_account_id}/media_publish"
                resp2 = await client.post(
                    publish_url,
                    params=self._auth_params(),
                    data={"creation_id": container_id},
                    headers=self._headers(),
                )
                data2 = resp2.json()

                if resp2.status_code in (200, 201) and "id" in data2:
                    post_id = data2["id"]
                    logger.info(f"Instagram Story published: {post_id}")
                    return {"success": True, "platform_post_id": post_id}

                err = data2.get("error", {}).get("message", resp2.text)
                logger.error(f"IG story publish failed: {err}")
                return {"success": False, "error": f"Story publish failed: {err}", "raw": data2}

            except Exception as e:
                logger.exception("Instagram story publish exception")
                return {"success": False, "error": str(e)}


    async def publish_instagram_carousel(self, caption: str, image_urls: List[str]) -> Dict[str, Any]:
        """
        Publish a carousel post (2-10 images).
        Fixes: convert each image if needed + poll each child + poll carousel container.
        """
        if not self.instagram_account_id:
            return {"success": False, "error": "INSTAGRAM_ACCOUNT_ID not configured"}

        if len(image_urls) < 2:
            return {"success": False, "error": "Carousel needs at least 2 images"}

        # Ensure IG compatible for all
        safe_urls: List[str] = []
        for u in image_urls[:10]:
            safe_urls.append(await self._ensure_ig_compatible_url(u))

        async with httpx.AsyncClient(timeout=240) as client:
            try:
                child_ids: List[str] = []

                # 1) Create each child container
                for idx, u in enumerate(safe_urls):
                    resp = await client.post(
                        f"{META_GRAPH_BASE}/{self.instagram_account_id}/media",
                        params=self._auth_params(),
                        data={
                            "image_url": u,
                            "is_carousel_item": "true",  # must be string
                        },
                        headers=self._headers(),
                    )
                    data = resp.json()

                    if resp.status_code in (200, 201) and "id" in data:
                        child_id = data["id"]
                        await self._wait_ig_container(client, child_id, timeout_s=240)
                        child_ids.append(child_id)
                    else:
                        err = data.get("error", {}).get("message", resp.text)
                        logger.error(f"IG carousel child {idx} failed: {err}")

                if len(child_ids) < 2:
                    return {"success": False, "error": "Not enough carousel children created"}

                # 2) Create carousel container
                resp2 = await client.post(
                    f"{META_GRAPH_BASE}/{self.instagram_account_id}/media",
                    params=self._auth_params(),
                    data={
                        "media_type": "CAROUSEL",
                        "caption": caption,
                        "children": ",".join(child_ids),
                    },
                    headers=self._headers(),
                )
                data2 = resp2.json()

                if resp2.status_code not in (200, 201) or "id" not in data2:
                    err = data2.get("error", {}).get("message", resp2.text)
                    return {"success": False, "error": f"Carousel container failed: {err}", "raw": data2}

                carousel_container_id = data2["id"]
                await self._wait_ig_container(client, carousel_container_id, timeout_s=300)

                # 3) Publish carousel
                resp3 = await client.post(
                    f"{META_GRAPH_BASE}/{self.instagram_account_id}/media_publish",
                    params=self._auth_params(),
                    data={"creation_id": carousel_container_id},
                    headers=self._headers(),
                )
                data3 = resp3.json()

                if resp3.status_code in (200, 201) and "id" in data3:
                    post_id = data3["id"]
                    logger.info(f"IG carousel published: {post_id}")
                    return {"success": True, "platform_post_id": post_id}

                err = data3.get("error", {}).get("message", resp3.text)
                return {"success": False, "error": f"Carousel publish failed: {err}", "raw": data3}

            except Exception as e:
                logger.exception("Instagram carousel exception")
                return {"success": False, "error": str(e)}

    async def publish_twitter(
        self,
        caption: str,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Post a tweet via Twitter API v2 using OAuth 1.0a (user context).
        If image_url is provided, the image is downloaded and uploaded via
        the v1.1 media/upload endpoint, then attached to the tweet.

        Requires in .env:
            X_CONSUMER_KEY, X_CONSUMER_SECRET
            X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
        """
        consumer_key    = settings.X_CONSUMER_KEY
        consumer_secret = settings.X_CONSUMER_SECRET
        access_token    = settings.X_ACCESS_TOKEN
        access_secret   = settings.X_ACCESS_TOKEN_SECRET

        if not all([consumer_key, consumer_secret, access_token, access_secret]):
            return {
                "success": False,
                "error": "Twitter credentials incomplete. Need X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET in .env",
            }

        try:
            import tweepy
        except ImportError:
            return {"success": False, "error": "tweepy not installed. Add 'tweepy==4.14.0' to requirements.txt and rebuild."}

        try:
            # Tweepy client for v2 (text tweets)
            client = tweepy.Client(
                consumer_key=consumer_key,
                consumer_secret=consumer_secret,
                access_token=access_token,
                access_token_secret=access_secret,
                wait_on_rate_limit=False,
            )

            media_id: Optional[str] = None

            # Upload image via v1.1 API if provided
            if image_url:
                try:
                    # Download image bytes
                    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
                        r = await http.get(image_url)
                        r.raise_for_status()
                        img_bytes = r.content

                    im = Image.open(BytesIO(img_bytes))
                    if im.mode not in ("RGB",):
                        im = im.convert("RGB")
                    buf = BytesIO()
                    im.save(buf, format="JPEG", quality=92)
                    buf.seek(0)

                    auth = tweepy.OAuth1UserHandler(
                        consumer_key, consumer_secret,
                        access_token, access_secret,
                    )
                    api_v1 = tweepy.API(auth)

                    media = api_v1.media_upload(filename="image.jpg", file=buf)
                    media_id = str(media.media_id)
                    logger.info(f"Twitter media uploaded: {media_id}")

                except Exception as img_err:
                    logger.warning(f"Twitter media upload failed: {img_err} — posting text-only")
                    media_id = None

            # Post tweet
            tweet_text = caption[:280]

            if media_id:
                response = client.create_tweet(text=tweet_text, media_ids=[media_id])
            else:
                response = client.create_tweet(text=tweet_text)

            tweet_id = str(response.data["id"])
            logger.info(f"Twitter tweet posted: {tweet_id}")
            return {"success": True, "platform_post_id": tweet_id}

        except Exception as e:
            logger.exception("Twitter publish exception")
            return {"success": False, "error": str(e)}


    async def publish_linkedin(
        self,
        caption: str,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Publish a post to LinkedIn using the UGC Posts API.
        If image_url is provided the image is downloaded, uploaded to LinkedIn,
        and attached to the post.  Falls back to text-only if the upload fails.
        """
        access_token = settings.LINKEDIN_ACCESS_TOKEN
        person_urn   = settings.LINKEDIN_PERSON_URN

        if not access_token:
            return {"success": False, "error": "LINKEDIN_ACCESS_TOKEN not configured"}
        if not person_urn:
            return {"success": False, "error": "LINKEDIN_PERSON_URN not configured"}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                asset_urn: Optional[str] = None

                # Step 1: upload image if provided
                if image_url:
                    try:
                        # 1a. Register upload
                        register_payload = {
                            "registerUploadRequest": {
                                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                                "owner": person_urn,
                                "serviceRelationships": [{
                                    "relationshipType": "OWNER",
                                    "identifier": "urn:li:userGeneratedContent",
                                }],
                            }
                        }
                        reg_resp = await client.post(
                            "https://api.linkedin.com/v2/assets?action=registerUpload",
                            headers=headers,
                            json=register_payload,
                        )
                        reg_data = reg_resp.json()

                        if reg_resp.status_code not in (200, 201):
                            raise RuntimeError(f"LinkedIn register upload failed: {reg_data}")

                        upload_url = (
                            reg_data["value"]["uploadMechanism"]
                            ["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]
                            ["uploadUrl"]
                        )
                        asset_urn = reg_data["value"]["asset"]

                        # 1b. Download image bytes
                        img_resp = await client.get(image_url, follow_redirects=True)
                        img_resp.raise_for_status()
                        img_bytes = img_resp.content

                        # Convert to JPEG if needed
                        try:
                            im = Image.open(BytesIO(img_bytes))
                            if im.mode not in ("RGB",):
                                im = im.convert("RGB")
                            buf = BytesIO()
                            im.save(buf, format="JPEG", quality=92)
                            img_bytes = buf.getvalue()
                        except Exception:
                            pass  # use as-is if PIL fails

                        # 1c. Upload bytes
                        upload_headers = {
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/octet-stream",
                        }
                        up_resp = await client.put(upload_url, headers=upload_headers, content=img_bytes)
                        if up_resp.status_code not in (200, 201):
                            logger.warning(f"LinkedIn image upload failed ({up_resp.status_code}), posting text-only")
                            asset_urn = None

                    except Exception as img_err:
                        logger.warning(f"LinkedIn image handling failed: {img_err} — posting text-only")
                        asset_urn = None

                # Step 2: build UGC post body
                if asset_urn:
                    media_block = {
                        "status": "READY",
                        "description": {"text": caption[:200]},
                        "media": asset_urn,
                        "title": {"text": caption[:100]},
                    }
                    share_content = {
                        "shareCommentary": {"text": caption},
                        "shareMediaCategory": "IMAGE",
                        "media": [media_block],
                    }
                else:
                    share_content = {
                        "shareCommentary": {"text": caption},
                        "shareMediaCategory": "NONE",
                    }

                post_payload = {
                    "author": person_urn,
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": share_content,
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
                    },
                }

                # ── Step 3: publish ──────────────────────────────────────
                post_resp = await client.post(
                    "https://api.linkedin.com/v2/ugcPosts",
                    headers=headers,
                    json=post_payload,
                )
                post_data = post_resp.json()

                if post_resp.status_code in (200, 201):
                    post_id = post_data.get("id", "")
                    logger.info(f"LinkedIn post published: {post_id}")
                    return {"success": True, "platform_post_id": post_id}

                err = post_data.get("message") or post_data.get("error", str(post_data))
                logger.error(f"LinkedIn publish failed: {err}")
                return {"success": False, "error": err, "raw": post_data}

            except Exception as e:
                logger.exception("LinkedIn publish exception")
                return {"success": False, "error": str(e)}


    async def publish_single(
        self,
        platform: str,
        content_type: str,
        caption: str,
        image_url: Optional[str] = None,
        story_image_url: Optional[str] = None,
        carousel_image_urls: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Publish a single platform + content_type combination.
        """
        if platform == "instagram":
            if content_type == "carousel" and carousel_image_urls and len(carousel_image_urls) >= 2:
                return await self.publish_instagram_carousel(caption, carousel_image_urls)
            if content_type == "story":
                use_url = story_image_url or image_url
                if use_url:
                    return await self.publish_instagram_story(use_url)
                return {"success": False, "error": "Instagram Story requires an image"}
            # feed (default)
            if image_url:
                return await self.publish_instagram(caption, image_url)
            return {"success": False, "error": "Instagram requires an image"}

        elif platform == "facebook":
            return await self.publish_facebook(caption, image_url)
        elif platform == "facebook_story":
            return await self.publish_facebook_story(image_url, caption)

        elif platform == "whatsapp":
            return await self.publish_whatsapp(caption, image_url)

        elif platform == "twitter":
            return await self.publish_twitter(caption, image_url)

        elif platform == "linkedin":
            return await self.publish_linkedin(caption, image_url)

        return {"success": False, "error": f"Platform '{platform}' not supported in publish_single"}


    async def publish_to_platforms(
        self,
        platforms: List[str],
        caption: str,
        image_url: Optional[str] = None,
        hashtags: Optional[List[str]] = None,
        carousel_image_urls: Optional[List[str]] = None,
        platform_captions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Publish a post to multiple platforms in one call.
        Returns per-platform results.
        """
        results: Dict[str, Any] = {}

        def _build_caption(platform: str) -> str:
            cap = (platform_captions or {}).get(platform, caption)
            if hashtags and platform in ("instagram", "facebook", "threads", "linkedin"):
                tag_str = " ".join(f"#{t}" for t in hashtags)
                return f"{cap}\n\n{tag_str}"
            return cap

        for platform in platforms:
            try:
                if platform == "whatsapp":
                    results["whatsapp"] = await self.publish_whatsapp(
                        caption=_build_caption("whatsapp"),
                        image_url=image_url,
                    )

                elif platform == "facebook":
                    results["facebook"] = await self.publish_facebook(
                        caption=_build_caption("facebook"),
                        image_url=image_url,
                    )

                elif platform == "instagram":
                    if carousel_image_urls and len(carousel_image_urls) >= 2:
                        results["instagram"] = await self.publish_instagram_carousel(
                            caption=_build_caption("instagram"),
                            image_urls=carousel_image_urls,
                        )
                    elif image_url:
                        results["instagram"] = await self.publish_instagram(
                            caption=_build_caption("instagram"),
                            image_url=image_url,
                        )
                    else:
                        results["instagram"] = {"success": False, "error": "Instagram requires an image"}

                elif platform == "twitter":
                    results["twitter"] = await self.publish_twitter(
                        caption=_build_caption("twitter"),
                        image_url=image_url,
                    )

                elif platform == "linkedin":
                    results["linkedin"] = await self.publish_linkedin(
                        caption=_build_caption("linkedin"),
                        image_url=image_url,
                    )

                else:
                    results[platform] = {"success": False, "error": f"Platform '{platform}' not yet supported"}

            except Exception as e:
                results[platform] = {"success": False, "error": str(e)}
                logger.exception(f"Publish to {platform} failed")

        return results