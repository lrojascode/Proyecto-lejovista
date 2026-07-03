import asyncio
import logging
from celery.utils.log import get_task_logger
from app.workers.celery_app import celery_app
from app.database import async_session
from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.ai_action import AIAction
from app.services.meta_ads.sync import MetaAdsSyncService
from app.services.ai.engine import AIEngine
from app.services.ai.executor import AIExecutor
from sqlalchemy import select

logger = get_task_logger(__name__)

def run_async(coro):
    """Ejecuta una corrutina asíncrona dentro del hilo síncrono de Celery."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

@celery_app.task(name="app.workers.tasks.schedule_all_syncs")
def schedule_all_syncs(sync_type: str = "INCREMENTAL"):
    """
    Busca todas las cuentas de anuncios activas y programa su sincronización.
    """
    async def _impl():
        async with async_session() as db:
            q = select(AdAccount).where(AdAccount.status == "ACTIVE")
            res = await db.execute(q)
            accounts = res.scalars().all()
            for acc in accounts:
                sync_ad_account.delay(acc.id, sync_type)
            logger.info(f"Programadas sincronizaciones para {len(accounts)} cuentas.")
    
    run_async(_impl())

@celery_app.task(name="app.workers.tasks.schedule_all_analyses")
def schedule_all_analyses():
    """
    Busca todas las organizaciones activas y programa su análisis semanal/diario de IA.
    """
    async def _impl():
        async with async_session() as db:
            q = select(Organization).where(Organization.status == "ACTIVE")
            res = await db.execute(q)
            orgs = res.scalars().all()
            for org in orgs:
                run_ai_analysis.delay(org.id)
            logger.info(f"Programados análisis de IA para {len(orgs)} organizaciones.")
            
    run_async(_impl())

@celery_app.task(name="app.workers.tasks.sync_ad_account")
def sync_ad_account(ad_account_id: str, sync_type: str = "INCREMENTAL"):
    """
    Sincroniza una cuenta de anuncios específica.
    """
    async def _impl():
        async with async_session() as db:
            acc = await db.get(AdAccount, ad_account_id)
            if not acc:
                logger.error(f"Cuenta de anuncios {ad_account_id} no encontrada para sync.")
                return
            
            sync_service = MetaAdsSyncService(db)
            logger.info(f"Iniciando sync {sync_type} para cuenta {acc.name} ({acc.id})...")
            job = await sync_service.run_sync(acc, sync_type)
            logger.info(f"Sync completada. Estado: {job.status}. Registros: {job.records_synced}")

            # Notificar al frontend por WebSocket (Redis Pub/Sub).
            # El canal se indexa por organization_id, que es como se conecta el cliente.
            try:
                from app.services.notification_service import NotificationService
                NotificationService().notify_job_status(
                    acc.organization_id, job.id, job.status, event_type="sync_status"
                )
            except Exception:
                logger.exception("No se pudo publicar la notificación de sync al WebSocket")

            # Al completar sync incremental o full, disparamos análisis de la organización
            if job.status == "COMPLETED":
                run_ai_analysis.delay(acc.organization_id)
                
    run_async(_impl())

@celery_app.task(name="app.workers.tasks.run_ai_analysis")
def run_ai_analysis(org_id: str):
    """
    Ejecuta el motor de análisis de IA para una organización.
    """
    async def _impl():
        async with async_session() as db:
            org = await db.get(Organization, org_id)
            if not org:
                logger.error(f"Organización {org_id} no encontrada para análisis.")
                return
                
            engine = AIEngine(db)
            logger.info(f"Iniciando análisis de IA para {org.name}...")
            result = await engine.run_analysis(org_id)
            logger.info(f"Análisis completado. Insights creados: {result['insights_count']}. Acciones propuestas: {result['actions_proposed']}")
            
    run_async(_impl())

@celery_app.task(name="app.workers.tasks.execute_action_task")
def execute_action_task(action_id: str):
    """
    Ejecuta una acción de IA aprobada de forma asíncrona.
    """
    async def _impl():
        async with async_session() as db:
            action = await db.get(AIAction, action_id)
            if not action:
                logger.error(f"Acción {action_id} no encontrada para ejecución.")
                return
                
            acc = await db.get(AdAccount, action.ad_account_id)
            if not acc:
                logger.error(f"Cuenta {action.ad_account_id} no encontrada para ejecutar acción.")
                return

            executor = AIExecutor(db)
            logger.info(f"Ejecutando acción {action.id} ({action.action_type}) en background...")
            updated_action = await executor.execute_action(acc, action)
            logger.info(f"Ejecución de acción {action.id} finalizada. Estado: {updated_action.status}")
            
    run_async(_impl())
