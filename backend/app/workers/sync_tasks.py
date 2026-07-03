import logging
from typing import List, Dict, Any
from celery import chord
from app.workers.celery_app import celery_app
import asyncio

# Las tareas de Celery (Síncronas por naturaleza en Celery, pero podemos usar async/await con asyncio.run o depender de librerías)
# Como la base de datos es async (asyncpg), usaremos una función helper para ejecutar código async dentro de celery tasks.

def async_to_sync(awaitable):
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(awaitable)

@celery_app.task(bind=True, max_retries=3)
def fetch_meta_data_task(self, tenant_id: str, ad_account_id: str, query: Dict[str, Any]):
    """
    Llama a Meta API para un endpoint específico.
    Si da 429 o 500, se reintenta automáticamente gracias a bind=True y autoretry_for (configurado abajo).
    """
    logger = logging.getLogger("celery.task")
    logger.info(f"Ejecutando query {query['query_id']} para tenant {tenant_id}")
    
    # En un entorno real, inicializaríamos la DB (async_session) y los servicios aquí.
    # from app.database import async_session
    # from app.services.meta_api_client import MetaApiClient
    # ...
    # response = async_to_sync(api_client.get(endpoint, ...))
    
    # Mock de la respuesta para la sesión 2
    return {
        "query_id": query["query_id"],
        "status": "success",
        "data": {"mock": "data"}
    }

@celery_app.task
def task_generate_master_brief(results: List[Dict[str, Any]], tenant_id: str, ad_account_id: str, sync_job_id: str):
    """
    Callback del chord. Se ejecuta cuando TODAS las fetch_meta_data_task terminan.
    """
    logger = logging.getLogger("celery.task")
    logger.info(f"Generando Master Brief para job {sync_job_id}")
    
    # Inicializar MasterBriefGenerator y generar el JSON
    # async_to_sync(generator.generate_brief(...))
    
    # Actualizar el SyncJob en base de datos a status="READY", e insertar el payload_json
    # En producción esto usaría la DB async
    
    # Notificar al frontend
    from app.services.notification_service import NotificationService
    notifier = NotificationService()
    notifier.notify_job_status(tenant_id, sync_job_id, "READY", event_type="sync_status")
    
    return {"status": "READY", "job_id": sync_job_id}

@celery_app.task
def task_sync_tenant_data(tenant_id: str, ad_account_id: str, page_id: str, sync_job_id: str):
    """
    Tarea principal que orquesta la sincronización.
    """
    from app.services.meta_sync_service import MetaSyncService
    
    logger = logging.getLogger("celery.task")
    logger.info(f"Iniciando sync_job {sync_job_id} para tenant {tenant_id}")
    
    sync_service = MetaSyncService(ad_account_id, page_id)
    queries = sync_service.generate_sync_plan()
    
    # Para espaciar y no saturar, usamos countdown.
    # 20 segundos entre cada petición (espaciado simulado para Rate Limit).
    tasks = []
    delay_seconds = 0
    for query in queries:
        # Las tareas se encolan para ejecutarse en el futuro
        tasks.append(
            fetch_meta_data_task.s(tenant_id, ad_account_id, query).set(countdown=delay_seconds)
        )
        delay_seconds += 20  # Añadir 20s para la siguiente llamada
        
    # Crear el chord: un grupo de tareas (header) seguido de un callback (body)
    callback = task_generate_master_brief.s(tenant_id, ad_account_id, sync_job_id)
    
    # Ejecutar chord
    chord(tasks)(callback)
