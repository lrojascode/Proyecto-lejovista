from celery import Celery
from app.config import settings

celery_app = Celery(
    "lejovista_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"]
)

# Configuración básica
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Lima",
    enable_utc=True,
    
    # Programación de Tareas Periódicas (Celery Beat)
    beat_schedule={
        # Sincronización incremental diaria a las 7:00 AM (Hora Lima)
        "daily-incremental-sync": {
            "task": "app.workers.tasks.schedule_all_syncs",
            "schedule": 86400.0,  # Cada 24 horas (para simplificar pruebas locales, o usar crontab)
            "args": ("INCREMENTAL",)
        },
        # Análisis diario de IA a las 8:00 AM
        "daily-ai-analysis": {
            "task": "app.workers.tasks.schedule_all_analyses",
            "schedule": 86400.0,
            "args": ()
        }
    }
)
