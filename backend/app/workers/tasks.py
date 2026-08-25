import asyncio
import logging
import random
from celery.utils.log import get_task_logger
from app.workers.celery_app import celery_app
from app.database import async_session, engine
from app.models.ad_account import AdAccount
from app.models.sync_job import SyncJob
from app.models.organization import Organization
from app.models.ai_action import AIAction
from app.models.meta_page import MetaPage
from app.services.meta_ads.sync import MetaAdsSyncService, SYNC_TYPE_BACKFILL
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

# El BACKFILL no dura lo que un sync: son decenas de meses, uno por vez, con las
# pausas del backoff de Meta en el medio. Con los 15 minutos del lock normal el
# candado caducaba a mitad de la corrida y el INCREMENTAL de las 06:00 entraba en
# paralelo sobre la misma cuenta — dos procesos haciendo el mismo
# "¿existe? no → insertar" sobre snapshots, que es exactamente la carrera que el
# lock existe para evitar.
BACKFILL_LOCK_TTL = 6 * 3600  # 6 h

# El FULL inicial termina antes de encolar el backfill, pero el lock se libera
# en el finally exterior. Un countdown corto evita que ambos trabajos compitan
# por esos últimos instantes y separa el volumen histórico del sync interactivo.
BACKFILL_AFTER_FULL_COUNTDOWN = 60


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

def _acquire_sync_lock(ad_account_id: str, ttl: int = SYNC_LOCK_TTL) -> bool:
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
                f"sync_running:{ad_account_id}", "1", nx=True, ex=ttl
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


async def _has_completed_history_backfill(
    db, organization_id: str, ad_account_id: str
) -> bool:
    """¿Está el histórico realmente completo?

    Antes esto preguntaba *"¿ya corrió un backfill alguna vez?"* buscando un
    `SyncJob BACKFILL COMPLETED`. Era frágil: un backfill cortado a la mitad
    quedaba marcado como hecho para siempre y la cuenta se quedaba sin historia
    sin que nadie se enterara.

    Ahora se compara el gasto que Meta reporta por mes contra el que tenemos en
    snapshots. Sobre una cuenta completa cuesta dos queries agregadas y cero
    llamadas a Meta, así que se puede consultar seguido; y si algún mes se
    pierde, la próxima corrida lo vuelve a pedir sola.
    """
    from app.services.history_reconciliation import get_history_status

    status = await get_history_status(db, organization_id, ad_account_id)
    # Sin histórico mensual todavía no hay contra qué comparar: no se puede
    # afirmar que está completo, así que se deja que el backfill corra.
    if status.months_total == 0:
        return False
    return status.is_complete


async def _schedule_history_backfill_after_full(db, ad_account: AdAccount) -> bool:
    """Encola una sola vez el backfill diario tras un FULL exitoso."""
    if await _has_completed_history_backfill(
        db, ad_account.organization_id, ad_account.id
    ):
        return False
    backfill_ad_account_history.apply_async(
        args=(ad_account.organization_id, ad_account.id),
        countdown=BACKFILL_AFTER_FULL_COUNTDOWN,
    )
    return True


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


@celery_app.task(name="app.workers.tasks.schedule_pending_history_backfills")
def schedule_pending_history_backfills(
    organization_id: str | None = None, ad_account_id: str | None = None
):
    """Encola backfills pendientes, escalonados dentro de la ventana del Beat.

    Corre en Beat y también a mano. En Beat cierra el caso de las cuentas YA
    conectadas: el backfill solo se encolaba colgado de un `FULL`, y un `FULL`
    solo ocurre al conectar o reconectar, así que una cuenta conectada antes de
    que el backfill existiera nunca lo iba a recibir sola. Es exactamente lo
    que dejó a la cuenta piloto con 36 días de historia.

    Es barato de repetir porque la reconciliación no llama a Meta cuando la
    cuenta ya está completa. Cada query conserva el scope de organización
    cuando se proporciona.
    """
    async def _impl():
        async with async_session() as db:
            q = select(AdAccount).where(AdAccount.status == "ACTIVE")
            if organization_id:
                q = q.where(AdAccount.organization_id == organization_id)
            if ad_account_id:
                q = q.where(AdAccount.id == ad_account_id)

            candidatas = (await db.execute(q)).scalars().all()
            # El filtro por "ya corrió un BACKFILL" se hacía en SQL con un
            # EXISTS. Ahora se reconcilia por cuenta: cuesta dos queries
            # agregadas y ninguna llamada a Meta, y detecta el caso que el
            # EXISTS no veía — un backfill que quedó a medias.
            accounts = [
                acc for acc in candidatas
                if not await _has_completed_history_backfill(db, acc.organization_id, acc.id)
            ]
            for acc in accounts:
                backfill_ad_account_history.apply_async(
                    args=(acc.organization_id, acc.id),
                    countdown=random.randint(0, BEAT_STAGGER_WINDOW_SECONDS),
                )
            logger.info(
                "Programados %d backfills históricos pendientes.", len(accounts)
            )
            return len(accounts)

    return run_async(_impl())

@celery_app.task(name="app.workers.tasks.refresh_org_pages")
def refresh_org_pages(org_id: str):
    """
    Refresca SOLO las páginas de Facebook + WhatsApp de las cuentas ACTIVE de
    una organización (sin sync completo). Disparada por
    POST /organizations/my/pages/refresh — el caso de uso es inmediato: el
    usuario acaba de otorgar acceso a una página nueva en el Business Manager
    y quiere verla en el dropdown sin esperar al sync de las 06:00.
    """
    run_async(refresh_org_pages_impl_wrapper(org_id))


async def refresh_org_pages_impl_wrapper(org_id: str):
    async with async_session() as db:
        await refresh_org_pages_impl(db, org_id)


async def refresh_org_pages_impl(db, org_id: str):
    """El trabajo real, separado de Celery para poder testearlo.

    La tarea envuelve todo en `asyncio.run`, que no se puede llamar desde un
    loop ya corriendo — o sea que la poda de páginas quedaba inalcanzable para
    un test. Un leak entre clientes sin test de regresión vuelve.
    """
    accounts = (await db.execute(
        select(AdAccount).where(
            AdAccount.organization_id == org_id,
            AdAccount.status == "ACTIVE",
        )
    )).scalars().all()

    sync_service = MetaAdsSyncService(db)
    vistas: set = set()
    alguna_ok = False
    for acc in accounts:
        try:
            vistas |= await sync_service.refresh_pages(acc)
            alguna_ok = True
        except Exception:
            # Una cuenta con token caído no debe impedir refrescar las demás.
            logger.exception("No se pudieron refrescar las páginas de la cuenta %s", acc.id)

    # Podar lo que ya no corresponde a esta organización.
    #
    # El guardado de páginas es puro upsert. Cuando el descubrimiento viejo
    # traía de más —recorría TODOS los Business Manager del token, así que a un
    # cliente le aparecían las páginas de los otros— acotarlo dejó de agregarlas,
    # pero las ya guardadas se quedaron para siempre. Es un leak entre clientes
    # que sobrevive a su propio arreglo, y sólo se limpia reconciliando.
    #
    # `alguna_ok` es la red de seguridad: si TODAS las cuentas fallaron (token
    # vencido, Meta caído) no se vio nada, y podar dejaría a la organización sin
    # ninguna página.
    podadas = 0
    if alguna_ok:
        sobrantes = (await db.execute(
            select(MetaPage).where(
                MetaPage.organization_id == org_id,
                MetaPage.status == "ACTIVE",
                MetaPage.external_page_id.notin_(vistas or [""]),
            )
        )).scalars().all()
        for pagina in sobrantes:
            # Se marca, no se borra: puede estar referenciada por campañas ya
            # creadas, y el listado de la UI ya filtra por ACTIVE.
            pagina.status = "REVOKED"
            podadas += 1
            logger.info(
                "La página %s (%s) ya no es accesible para la org %s: se marca REVOKED.",
                pagina.external_page_id, pagina.name, org_id,
            )
        await db.commit()

    logger.info(
        "Refresh de páginas de la org %s: %d cuentas, %d páginas vigentes, %d dadas de baja.",
        org_id, len(accounts), len(vistas), podadas,
    )


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
                if sync_type == "FULL":
                    await _schedule_history_backfill_after_full(db, acc)
                _schedule_org_analysis_debounced(acc.organization_id)

    try:
        run_async(_impl())
    finally:
        # Siempre liberar: si se propaga un TransientMetaError, Celery reintenta
        # y ese reintento necesita poder tomar el lock de nuevo.
        _release_sync_lock(ad_account_id)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.backfill_ad_account_history",
    autoretry_for=(TransientMetaError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def backfill_ad_account_history(
    self, organization_id: str, ad_account_id: str
):
    """Backfill diario idempotente y separado del sync habitual.

    Solo lee Meta. Usa el mismo lock por cuenta que FULL/INCREMENTAL y consulta
    por organización para que un id de otra tenant no pueda cruzar datos.
    """
    if not _acquire_sync_lock(ad_account_id, ttl=BACKFILL_LOCK_TTL):
        raise self.retry(countdown=60, max_retries=10)

    async def _impl():
        async with async_session() as db:
            acc = (await db.execute(
                select(AdAccount).where(
                    AdAccount.id == ad_account_id,
                    AdAccount.organization_id == organization_id,
                    AdAccount.status == "ACTIVE",
                )
            )).scalar_one_or_none()
            if not acc:
                logger.error(
                    "Cuenta activa %s no encontrada en org %s para backfill.",
                    ad_account_id, organization_id,
                )
                return "NOT_FOUND"

            if await _has_completed_history_backfill(
                db, organization_id, ad_account_id
            ):
                logger.info("Backfill histórico ya completado para %s; se omite.", ad_account_id)
                return "ALREADY_COMPLETED"

            logger.info("Iniciando backfill histórico para %s.", ad_account_id)
            job = await MetaAdsSyncService(db).run_sync(acc, SYNC_TYPE_BACKFILL)
            # PARTIAL cuenta igual que COMPLETED acá: bajó meses de verdad, así
            # que el reporte cacheado ya no refleja la realidad aunque falten
            # otros meses.
            if job.status in ("COMPLETED", "PARTIAL"):
                try:
                    from app.services.report_cache import invalidate as invalidate_report_cache
                    await invalidate_report_cache(acc.id)
                except Exception:
                    logger.exception("No se pudo invalidar el cache tras el backfill")
                _schedule_org_analysis_debounced(organization_id)
            logger.info(
                "Backfill histórico finalizado para %s: %s (%s registros).",
                ad_account_id, job.status, job.records_synced,
            )
            return job.status

    try:
        return run_async(_impl())
    finally:
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


@celery_app.task(name="app.workers.tasks.check_expiring_tokens_task")
def check_expiring_tokens_task():
    """Fase 6.4 (Roadmap 7): Inspecciona tokens de acceso de Meta Ads y emite
    avisos anticipados (< 7 días) o alertas de expiración vía WebSocket y email.
    Corre diariamente vía Celery Beat (08:00 Lima)."""
    async def _impl():
        from app.services.meta_ads.token_inspection import check_expiring_tokens
        async with async_session() as db:
            results = await check_expiring_tokens(db)
            logger.info(f"Inspección de tokens de Meta completada: {len(results)} cuentas evaluadas.")

    run_async(_impl())

