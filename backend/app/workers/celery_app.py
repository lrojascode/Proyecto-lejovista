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
        "app.workers.creation_tasks",
    ]
)

# Colas dedicadas (I-2): antes todo corría en una sola cola/worker. Con P-2 real
# haciendo ~12-15 llamadas a Meta por sync (vs. ~5-8 antes), un pico de syncs podía
# dejar sin turno a la ejecución de acciones de IA (escrituras aprobadas por el
# usuario) o a la creación de campañas. Cada tipo de trabajo va a su propia cola;
# los workers se escalan de forma independiente (ver docker-compose.yml).
_TASK_ROUTES = {
    # Sincronización con Meta (lecturas: cuentas, campañas, adsets, breakdowns...).
    "app.workers.tasks.schedule_all_syncs": {"queue": "sync"},
    "app.workers.tasks.sync_ad_account": {"queue": "sync"},
    "app.workers.tasks.schedule_pending_history_backfills": {"queue": "sync"},
    "app.workers.tasks.backfill_ad_account_history": {"queue": "sync"},
    # Motor de IA (análisis + ejecución de acciones aprobadas contra Meta).
    "app.workers.tasks.schedule_all_analyses": {"queue": "ai"},
    "app.workers.tasks.run_ai_analysis": {"queue": "ai"},
    "app.workers.tasks.execute_action_task": {"queue": "ai"},
    # Outcome loop del aprendizaje (Fase 1, C-1): evalúa resultados reales.
    "app.workers.tasks.evaluate_campaign_outcomes": {"queue": "ai"},
    # Creación de campañas/adsets/creativos/anuncios (flujo de escritura por pasos).
    "app.workers.creation_tasks.*": {"queue": "creation"},
    # Mantenimiento de datos (D-3): sin apuro, va a la cola por defecto.
    "app.workers.tasks.archive_old_snapshots_task": {"queue": "celery"},
    # Aviso anticipado de expiración de token Meta (Roadmap 7 / Fase 6.4)
    "app.workers.tasks.check_expiring_tokens_task": {"queue": "sync"},
}

# Configuración básica
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Lima",
    enable_utc=True,
    task_routes=_TASK_ROUTES,
    task_default_queue="celery",

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
        },
        # Outcome loop (C-1): evalúa a las 8:00 (tras el sync de las 6:00, que
        # deja los snapshots frescos con los que se mide cada campaña creada).
        "daily-campaign-outcomes": {
            "task": "app.workers.tasks.evaluate_campaign_outcomes",
            "schedule": crontab(hour=8, minute=0),
            "args": ()
        },
        # Roadmap 7 (Fase 6.4): Verificación diaria de expiración de tokens Meta (8:00 AM)
        "daily-check-expiring-tokens": {
            "task": "app.workers.tasks.check_expiring_tokens_task",
            "schedule": crontab(hour=8, minute=0),
            "args": ()
        },
        # D-3: archivado semanal de snapshots viejos (domingo 3:00 AM, hora Lima).
        "weekly-snapshot-archival": {
            "task": "app.workers.tasks.archive_old_snapshots_task",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),
            "args": ()
        }
    }
)
