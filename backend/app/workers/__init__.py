from celery import Celery
from app.config import settings

celery_app = Celery(
    "lejovista_workers",
    broker=settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND_URL,
    include=["app.workers.sync_tasks", "app.workers.creation_tasks", "app.workers.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    result_expires=3600, # 1 hour
)
