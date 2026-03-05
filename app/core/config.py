from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    MONGODB_URI: str
    MONGODB_DB_NAME: str = "binayah_news_v2"
    OPENAI_API_KEY: str
    STABILITY_API_KEY: Optional[str] = ""
    NEWSAPI_KEY: Optional[str] = None
    STORAGE_BACKEND: str = "dual"
    STORAGE_LOCAL_PATH: str = "/app/storage"
    STORAGE_KEEP_LOCAL: bool = True
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_BUCKET: str = ""
    AWS_S3_REGION: str = "ap-south-1"
    REDIS_URL: str = "redis://redis:6379/0"
    META_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_DEFAULT_RECIPIENTS: List[str] = []
    WANOTIFIER_API_KEY: str = ""
    FACEBOOK_PAGE_ID: str = ""
    FACEBOOK_ACCESS_TOKEN: str = ""
    INSTAGRAM_ACCOUNT_ID: str = ""
    INSTAGRAM_ACCESS_TOKEN: str = ""
    LINKEDIN_ACCESS_TOKEN: str = ""
    LINKEDIN_PERSON_URN: str = ""
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    X_CONSUMER_KEY: str = ""
    X_CONSUMER_SECRET: str = ""
    X_ACCESS_TOKEN: str = ""
    X_ACCESS_TOKEN_SECRET: str = ""
    X_BEARER_TOKEN: str = ""
    DASHBOARD_USERNAME: str = "admin"
    DASHBOARD_PASSWORD: str = "binayah2026"
    AUTH_TOKEN_HOURS: int = 72
    # Scheduler: Dubai 9am,12pm,7pm = UTC 5,8,15
    SCHEDULE_HOURS_UTC: str = "5,8,15"
    POSTS_TO_GENERATE: int = 3
    POSTS_TO_AUTOPOST: int = 1
    # test = auto-post to test tokens | manual = hold as draft | production = live
    AUTO_POST_MODE: str = "test"
    PROD_META_ACCESS_TOKEN: str = ""
    PROD_FACEBOOK_PAGE_ID: str = ""
    PROD_FACEBOOK_ACCESS_TOKEN: str = ""
    PROD_INSTAGRAM_ACCOUNT_ID: str = ""
    PROD_INSTAGRAM_ACCESS_TOKEN: str = ""
    # Image: "nanobanana" (cheap/test) or "stability" (quality/prod)
    IMAGE_PROVIDER: str = "nanobanana"
    NANOBANANA_API_KEY: str = ""
    STABILITY_VARIANT: str = "core"
    BRAND_PRIMARY_COLOR: str = "#004e41"
    BRAND_SECONDARY_COLOR: str = "#d1ae4a"
    BRAND_NAME: str = "Binayah Properties"
    API_BASE_URL: str = ""
    FRONTEND_URL: str = ""
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

settings = Settings()
