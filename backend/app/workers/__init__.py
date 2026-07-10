# Re-exporta la app real de Celery (celery_app.py: colas, rutas y beat_schedule).
# Antes este __init__ creaba una SEGUNDA instancia de Celery con otra config
# (sin task_routes ni beat_schedule) que competía con la de celery_app.py al
# resolver `celery -A app.workers` — y era el único sitio que aún incluía el
# stub app.workers.sync_tasks (A-2).
from app.workers.celery_app import celery_app

__all__ = ["celery_app"]
