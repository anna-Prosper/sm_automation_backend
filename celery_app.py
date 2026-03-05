from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

app = Celery('binayah_news', broker=settings.REDIS_URL)

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Dubai',
    enable_utc=True,
)

app.conf.beat_schedule = {
    # Generate new posts 3× daily (07:00, 13:00, 19:00 Dubai)
    'fetch-and-generate-morning': {
        'task': 'app.workers.tasks.fetch_and_generate_posts',
        'schedule': crontab(hour=7, minute=0),
    },
    'fetch-and-generate-afternoon': {
        'task': 'app.workers.tasks.fetch_and_generate_posts',
        'schedule': crontab(hour=13, minute=0),
    },
    'fetch-and-generate-evening': {
        'task': 'app.workers.tasks.fetch_and_generate_posts',
        'schedule': crontab(hour=19, minute=0),
    },

    # Auto-publish approved high-score posts every 4 hours
    'auto-publish-top-posts': {
        'task': 'app.workers.tasks.auto_publish_top_posts',
        'schedule': crontab(hour='*/4'),
    },

    # Publish scheduled posts — check every 2 minutes
    'publish-scheduled-posts': {
        'task': 'app.workers.tasks.publish_scheduled_posts',
        'schedule': crontab(minute='*/2'),
    },

    # Clean up old dedup hashes at 3 AM
    'cleanup-old-posts': {
        'task': 'app.workers.tasks.cleanup_old_posts',
        'schedule': crontab(hour=3, minute=0),
    },
}
