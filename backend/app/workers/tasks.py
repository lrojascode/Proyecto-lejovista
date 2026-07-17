import asyncio
import logging
import random
from celery.utils.log import get_task_logger
from app.workers.celery_app import celery_app
from app.database import async_session, engine
from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.ai_action import AIAction
from app.services.meta_ads.sync import MetaAdsSyncService
from app.services.meta_ads.exceptions import TransientMetaError
from app.services.ai.engine import AIEngine
from app.services.ai.executor import AIExecutor
from app.services.snapshot_archiver import archive_old_snapshots
from app.config import settings
from sqlalchemy import select

logger = get_task_logger(__name__)

# Ventana de escalonado (segundos) para repartir el fan-out del Beat y no
# golpear el rate limit de Meta con N cuentas sincronizando al mismo tiempo.
BEAT_STAGGER_WINDOW_SECONDS = 1800  # 30 min

# Debounce del análisis post-sync (D-1): una org con N cuentas disparaba N
# análisis completos (cada uno itera TODAS las cuentas y llama al LLM por cada
# una → hasta N² llamadas). Con el lock, el primer sync que completa agenda UN
# análisis diferido y los demás no encolan nada.
ANALYSIS_DEBOUNCE_COUNTDOWN = 300   # el análisis corre 5 min después del 1er sync
ANALYSIS_DEBOUNCE_TTL = 360         # el lock vive un poco más que el countdown

# TTL del lock de sync por cuenta (ver _acquire_sync_lock). Holgado respecto a
# lo que tarda un sync real (~15 s en cuentas chicas, más en cuentas grandes con
# muchas campañas) pero acotado para que un worker muerto no deje la cuenta
# bloqueada indefinidamente.
SYNC_LOCK_TTL = 900  # 15 min


def _schedule_org_analysis_debounced(org_id: str) -> bool:
    """Encola run_ai_analysis para la org solo si no hay ya uno pendiente.

    Usa Redis SET NX EX como lock. Fail-open: si Redis no responde, se encola
    igual (preferimos un análisis de más a perder el análisis).
    Devuelve True si se encoló.
    """
    import redis as redis_sync  # cliente síncrono: esto corre en contexto Celery

    try:
        client = redis_sync.Redis.from_url(settings.REDIS_URL)
        try:
            acquired = client.set(
                f"analysis_pending:{org_id}", "1", nx=True, ex=ANALYSIS_DEBOUNCE_TTL
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
        if not acquired:
            logger.info(f"Análisis para org {org_id} ya pendiente; no se encola otro (debounce).")
            return False
    except Exception as e:
        logger.warning(f"Redis no disponible para debounce ({e}); encolando análisis igual.")

    run_ai_analysis.apply_async(args=(org_id,), countdown=ANALYSIS_DEBOUNCE_COUNTDOWN)
    return True


def _release_analysis_lock(org_id: str) -> None:
    """Libera el lock del debounce al INICIAR el análisis, para que un sync que
    complete durante la corrida pueda agendar el siguiente. Fail-silent."""
    import redis as redis_sync

    try:
        client = redis_sync.Redis.from_url(settings.REDIS_URL)
        try:
            client.delete(f"analysis_pending:{org_id}")
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception:
        pass

def _acquire_sync_lock(ad_account_id: str) -> bool:
    """Lock por CUENTA para que no corran dos syncs a la vez sobre la misma.

    El worker `sync` tiene concurrency 2, así que dos syncs de la misma cuenta
    (p. ej. el FULL que dispara reconectarla + un INCREMENTAL manual segundos
    después) se pisaban: ambos hacían "¿existe este creativo? no → insertar" y
    el segundo reventaba con UniqueViolationError (uq_creative_acc_hash). El
    patrón check-then-insert del sync no es seguro entre procesos; serializar
    por cuenta lo resuelve de raíz, y de paso protege los demás upserts
    (snapshots, páginas, audiencias) de la misma carrera.

    Fail-open: si Redis no responde se sincroniza igual (preferimos arriesgar
    la carrera antes que dejar de sincronizar).
    """
    import redis as redis_sync

    try:
        client = redis_sync.Redis.from_url(settings.REDIS_URL)
        try:
            return bool(client.set(
                f"sync_running:{ad_account_id}", "1", nx=True, ex=SYNC_LOCK_TTL
            ))
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Redis no disponible para el lock de sync ({e}); sincronizando igual.")
        return True


def _release_sync_lock(ad_account_id: str) -> None:
    """Libera el lock de sync de la cuenta. Fail-silent."""
    import redis as redis_sync

    try:
        client = redis_sync.Redis.from_url(settings.REDIS_URL)
        try:
            client.delete(f"sync_running:{ad_account_id}")
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception:
        pass


def run_async(coro):
    """Ejecuta una corrutina en un event loop nuevo y limpio por tarea.

    Usa asyncio.run (loop creado y cerrado limpiamente) en vez de reutilizar
    get_event_loop, que causaba errores intermitentes ('Event loop is closed').
    Al terminar se descarta el pool del engine para no reutilizar conexiones
    asyncpg ligadas a un loop ya cerrado.
    """
    async def _runner():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_runner())

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
                # Escalonar con jitter para no saturar el rate limit de Meta.
                sync_ad_account.apply_async(
                    args=(acc.id, sync_type),
                    countdown=random.randint(0, BEAT_STAGGER_WINDOW_SECONDS)
                )
            logger.info(f"Programadas sincronizaciones (escalonadas) para {len(accounts)} cuentas.")

    run_async(_impl())

@celery_app.task(name="app.workers.tasks.refresh_org_pages")
def refresh_org_pages(org_id: str):
    """
    Refresca SOLO las páginas de Facebook + WhatsApp de las cuentas ACTIVE de
    una organización (sin sync completo). Disparada por
    POST /organizations/my/pages/refresh — el caso de uso es inmediato: el
    usuario acaba de otorgar acceso a una página nueva en el Business Manager
    y quiere verla en el dropdown sin esperar al sync de las 06:00.
    """
    async def _impl():
        async with async_session() as db:
            q = select(AdAccount).where(
                AdAccount.organization_id == org_id,
                AdAccount.status == "ACTIVE",
            )
            res = await db.execute(q)
            accounts = res.scalars().all()
            sync_service = MetaAdsSyncService(db)
            for acc in accounts:
                try:
                    await sync_service.refresh_pages(acc)
                except Exception:
                    # Una cuenta con token caído no debe impedir refrescar las demás.
                    logger.exception("No se pudieron refrescar las páginas de la cuenta %s", acc.id)
            logger.info("Refresh de páginas completado para %d cuentas de la org %s.",
                        len(accounts), org_id)

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
                run_ai_analysis.apply_async(
                    args=(org.id,),
                    countdown=random.randint(0, BEAT_STAGGER_WINDOW_SECONDS)
                )
            logger.info(f"Programados análisis de IA (escalonados) para {len(orgs)} organizaciones.")

    run_async(_impl())

@celery_app.task(
    bind=True,
    name="app.workers.tasks.sync_ad_account",
    autoretry_for=(TransientMetaError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def sync_ad_account(self, ad_account_id: str, sync_type: str = "INCREMENTAL"):
    """
    Sincroniza una cuenta de anuncios específica.
    Reintenta automáticamente ante errores transitorios de Meta (429/5xx/red).

    Serializado por cuenta con un lock (ver `_acquire_sync_lock`): dos syncs
    simultáneos de la misma cuenta se pisaban en los upserts del inventario.
    """
    if not _acquire_sync_lock(ad_account_id):
        logger.info(
            "Ya hay un sync en curso para la cuenta %s; se omite este %s.",
            ad_account_id, sync_type,
        )
        return

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

            # Al completar sync incremental o full, disparamos análisis de la
            # organización — con debounce (D-1): si otra cuenta de la misma org
            # ya lo agendó, no se encola otro.
            if job.status == "COMPLETED":
                # 4.2: invalidar el cache del ReportBuilder de esta cuenta — los
                # snapshots cambiaron, el reporte cacheado ya no refleja la realidad.
                try:
                    from app.services.report_cache import invalidate as invalidate_report_cache
                    await invalidate_report_cache(acc.id)
                except Exception:
                    logger.exception("No se pudo invalidar el cache de reporte tras el sync")
                _schedule_org_analysis_debounced(acc.organization_id)

    try:
        run_async(_impl())
    finally:
        # Siempre liberar: si se propaga un TransientMetaError, Celery reintenta
        # y ese reintento necesita poder tomar el lock de nuevo.
        _release_sync_lock(ad_account_id)

@celery_app.task(name="app.workers.tasks.run_ai_analysis")
def run_ai_analysis(org_id: str):
    """
    Ejecuta el motor de análisis de IA para una organización.
    """
    async def _impl():
        # Liberar el lock del debounce (D-1): a partir de aquí, un sync nuevo
        # puede agendar el siguiente análisis sin quedar bloqueado por este.
        _release_analysis_lock(org_id)

        async with async_session() as db:
            org = await db.get(Organization, org_id)
            if not org:
                logger.error(f"Organización {org_id} no encontrada para análisis.")
                return

            engine = AIEngine(db)
            logger.info(f"Iniciando análisis de IA para {org.name}...")
            result = await engine.run_analysis(org_id)
            logger.info(f"Análisis completado. Insights creados: {result['insights_count']}. Acciones propuestas: {result['actions_proposed']}")

            # Notificar por WebSocket para que Diagnósticos/Acciones se
            # refresquen solas (antes no había ningún evento para esto — el
            # análisis corre automático encadenado tras cada sync, pero el
            # frontend no tenía forma de enterarse sin recargar la página).
            try:
                from app.services.notification_service import NotificationService
                NotificationService().notify_job_status(
                    org_id, org_id, "COMPLETED", event_type="analysis_status",
                    extra={"insights_count": result["insights_count"], "actions_proposed": result["actions_proposed"]},
                )
            except Exception:
                logger.exception("No se pudo publicar la notificación de análisis al WebSocket")

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

@celery_app.task(name="app.workers.tasks.evaluate_campaign_outcomes")
def evaluate_campaign_outcomes():
    """Fase 1 (C-1): cierra el ciclo de aprendizaje. Corre diario vía Beat
    (8:00 Lima, después del sync de las 6:00 que trae los snapshots frescos):
    evalúa cada CampaignLearningLog CREATED cuya ventana ya venció y persiste
    el veredicto real (WIN/LOSS/NEUTRAL/NOT_ACTIVATED) en outcome_json."""
    async def _impl():
        from app.services.campaign_outcome import evaluate_pending_outcomes
        async with async_session() as db:
            counts = await evaluate_pending_outcomes(db)
            logger.info(f"Evaluación de outcomes completada: {counts}")

    run_async(_impl())


@celery_app.task(name="app.workers.tasks.archive_old_snapshots_task")
def archive_old_snapshots_task():
    """D-3: mueve a las tablas de archivo los snapshots más viejos que
    SNAPSHOT_RETENTION_DAYS, para que campaign/adset/ad_snapshots no crezcan
    sin límite. Corre semanalmente vía Celery Beat."""
    async def _impl():
        async with async_session() as db:
            counts = await archive_old_snapshots(db, settings.SNAPSHOT_RETENTION_DAYS)
            logger.info(f"Archivado de snapshots completado: {counts}")

    run_async(_impl())
