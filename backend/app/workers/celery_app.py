import logging
from celery import Celery
from celery.schedules import crontab
from app.config import settings

# Sentry opcional en el worker (aquí ocurren los fallos de sync/IA que hay que ver).
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)
    except Exception:
        logging.getLogger("lejovista.celery").warning("No se pudo inicializar Sentry en el worker.")

celery_app = Celery(
    "lejovista_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.tasks",
        "app.workers.sync_tasks",
        "app.workers.creation_tasks",
    ]
)

# Configuración básica
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Lima",
    enable_utc=True,
    
    # Programación de Tareas Periódicas (Celery Beat).
    # timezone=America/Lima (arriba) → crontab evalúa en hora local, sin derivar
    # con reinicios del contenedor. El fan-out se escalona con jitter en las tareas.
    beat_schedule={
        # Sincronización incremental diaria a las 6:00 AM (Hora Lima)
        "daily-incremental-sync": {
            "task": "app.workers.tasks.schedule_all_syncs",
            "schedule": crontab(hour=6, minute=0),
            "args": ("INCREMENTAL",)
        },
        # Análisis diario de IA a las 7:00 AM (tras completar el sync)
        "daily-ai-analysis": {
            "task": "app.workers.tasks.schedule_all_analyses",
            "schedule": crontab(hour=7, minute=0),
            "args": ()
        }
    }
)
