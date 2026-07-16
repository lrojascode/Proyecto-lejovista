import json
import logging
from typing import Dict, Any
from datetime import datetime, timedelta

from app.workers.celery_app import celery_app
from app.workers.tasks import run_async
from app.database import async_session
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.campaign_draft import CampaignDraft
from app.tenant_rules.campaign_defaults import get_campaign_rules_for_org
from app.services.brand_code import derive_brand_code as _brand_code
from app.services.campaign_targeting import build_targeting
from app.services.creative_builder import CreativeBuilder
from app.services.meta_asset_repository import MetaAssetRepository
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter
from app.services.meta_ads.exceptions import TransientMetaError, classify_meta_error

# En Celery, usamos el argumento previo que devuelve la tarea en el chain.
# Por lo tanto, cada tarea recibe `context` como primer argumento (excepto la primera, si no se le pasa)
# y debe retornar el contexto actualizado.

logger = logging.getLogger("celery.task")


async def _get_ad_account(db, ad_account_id: str) -> AdAccount:
    acc = await db.get(AdAccount, ad_account_id)
    if not acc:
        # Fallo permanente: no existe la cuenta, no tiene sentido reintentar.
        raise Exception(f"AdAccount {ad_account_id} no encontrada.")
    return acc


async def _get_campaign_rules(db, acc: AdAccount) -> Dict[str, Any]:
    """M-2: reglas de campaña resueltas para la org de `acc` (con overrides
    si los tiene)."""
    org = await db.get(Organization, acc.organization_id)
    return get_campaign_rules_for_org(org)



def _raise_if_meta_error(result: Dict[str, Any]) -> None:
    """Si `result` es una respuesta de error de Meta, la clasifica y la lanza
    (transitoria => Celery reintenta vía autoretry_for; permanente => corta el
    chain y cae en `task_handle_chain_error`)."""
    if not isinstance(result, dict) or "error" in result or "ERROR" in result:
        raise classify_meta_error(Exception(json.dumps(result)))


async def _merge_draft_result(
    db,
    draft_id: str,
    name: str | None = None,
    status: str | None = None,
    error_message: str | None = None,
    **result_fields: Any,
) -> None:
    """Persiste el avance de la Saga en el `CampaignDraft` real.

    Antes `draft_id` era un uuid suelto que nunca se guardaba en ningún lado —
    el frontend "simulaba" el polling con un `setInterval` que siempre
    terminaba en éxito (Fase 1 del plan de creación de campañas). Cada paso
    del chain llama a esto para ir dejando los IDs de Meta (campaign_id,
    adset_id, creative_id, ad_id) en `payload["result"]`.
    """
    draft = await db.get(CampaignDraft, draft_id)
    if not draft:
        logger.warning(f"CampaignDraft {draft_id} no encontrado al actualizar el progreso de la Saga.")
        return

    if result_fields:
        payload = dict(draft.payload or {})
        result = dict(payload.get("result", {}))
        result.update(result_fields)
        payload["result"] = result
        draft.payload = payload
    if name:
        draft.name = name
    if status:
        draft.status = status
    if error_message is not None:
        draft.error_message = error_message

    db.add(draft)
    await db.commit()


_RETRY_KWARGS = dict(
    bind=True,
    autoretry_for=(TransientMetaError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)


@celery_app.task(**_RETRY_KWARGS)
def task_upload_image(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 1: Subir imagen o reutilizar hash.
    Usa MetaAssetRepository (POST multipart real a /adimages, gateado por
    SIMULATE_META_WRITES), no el MetaApiClient del validador síncrono.
    """
    logger.info(f"Task 1 (Upload Image) - context: {context}")

    async def _impl():
        async with async_session() as db:
            acc = await _get_ad_account(db, context["ad_account_id"])

            # Video/UGC (playbook §5 Paso 4): si ya hay video_id + thumbnail_url,
            # no hay imagen que subir — el creativo usará video_data en su lugar.
            if context.get("video_id"):
                await _merge_draft_result(
                    db, context["draft_id"],
                    video_id=context["video_id"], thumbnail_url=context.get("thumbnail_url"),
                )
                return context

            image_hash = context.get("image_hash")
            if not image_hash:
                limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
                repo = MetaAssetRepository(limiter)
                image_hash = await repo.get_or_upload_image(
                    ad_account_external_id=acc.external_account_id,
                    access_token=acc.get_access_token(),
                    image_url=context.get("image_url"),
                    image_path=context.get("image_path"),
                    image_hash=None,
                )
            context["image_hash"] = image_hash
            await _merge_draft_result(db, context["draft_id"], image_hash=image_hash)
            return context

    return run_async(_impl())


@celery_app.task(**_RETRY_KWARGS)
def task_create_campaign(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 2: Crear campaña (PAUSED) — POST real a `/{act}/campaigns` vía
    MetaAPIRateLimiter, gateado por SIMULATE_META_WRITES.
    """
    logger.info(f"Task 2 (Create Campaign) - context: {context}")

    async def _impl():
        async with async_session() as db:
            acc = await _get_ad_account(db, context["ad_account_id"])
            rules = await _get_campaign_rules(db, acc)

            product_name = context["product_name"]
            month_year = datetime.now().strftime("%b%Y").upper()
            brand = _brand_code(acc)
            campaign_name = f"{brand}_MSJ_{product_name}_{month_year}".replace(" ", "")

            payload = {
                "name": campaign_name,
                "objective": rules["objective"],
                "buying_type": "AUCTION",
                "status": rules["default_status"],
                "special_ad_categories": "[]",
                # El presupuesto vive a nivel de adset (ABO), no de campaña.
                # En ese caso Meta EXIGE declarar is_adset_budget_sharing_enabled
                # explícitamente (error_subcode 4834011 si falta). "false" = los
                # adsets no comparten presupuesto entre sí — el comportamiento
                # esperado para nuestras campañas de un solo adset. Se manda como
                # string en minúscula porque el POST es form-encoded (un bool de
                # Python se serializaría como "False" y Meta no lo parsea).
                "is_adset_budget_sharing_enabled": "false",
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
            endpoint = f"{acc.external_account_id}/campaigns"
            result = await limiter.call_api(
                endpoint, payload, method="POST", access_token=acc.get_access_token()
            )
            _raise_if_meta_error(result)

            campaign_id = (
                f"mock_camp_{int(datetime.now().timestamp())}"
                if settings.simulate_meta_writes_for(acc.id)
                else result.get("id")
            )
            if not campaign_id:
                raise Exception(f"Meta no devolvió id de campaña: {result}")

            context["campaign_id"] = campaign_id
            context["campaign_name"] = campaign_name
            await _merge_draft_result(
                db, context["draft_id"], name=campaign_name,
                campaign_id=campaign_id, campaign_name=campaign_name,
            )
            return context

    return run_async(_impl())


@celery_app.task(**_RETRY_KWARGS)
def task_create_adset(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 3: Crear AdSet (Geo, placements, WhatsApp) — POST real a `/{act}/adsets`.
    """
    logger.info(f"Task 3 (Create AdSet) - context: {context}")

    async def _impl():
        async with async_session() as db:
            acc = await _get_ad_account(db, context["ad_account_id"])
            rules = await _get_campaign_rules(db, acc)
            campaign_id = context["campaign_id"]

            start_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0000")
            end_time = (datetime.now() + timedelta(days=context["days"])).strftime("%Y-%m-%dT%H:%M:%S+0000")

            payload = {
                "name": f"{context['campaign_name']}_AS01",
                "campaign_id": campaign_id,
                "optimization_goal": rules["optimization_goal"],
                "destination_type": rules["destination_type"],
                "billing_event": "IMPRESSIONS",
                "bid_strategy": rules["bid_strategy"],
                "lifetime_budget": int(context["budget"] * 100),  # Meta espera centavos
                "start_time": start_time,
                "end_time": end_time,
                # Regla de oro del playbook (#3): todo se crea PAUSED. La activación
                # la hace el humano manualmente en Ads Manager — nunca por API.
                "status": rules["default_status"],
                # Targeting optimizado desde el histórico ganador (Ajuste 2):
                # edad/dispositivo/placements salen del `CampaignSuggester`, no
                # hardcodeados. Fallback a defaults si la cuenta no tiene histórico.
                "targeting": json.dumps(build_targeting(
                    best_age_range=context.get("best_age_range"),
                    best_device=context.get("best_device"),
                    excluded_placements=context.get("excluded_placements") or [],
                    rules=rules,
                )),
                # CTWA: la Marketing API v25 documenta el promoted_object del
                # adset como SOLO page_id — el número de WhatsApp lo toma del
                # que esté conectado a la página en Meta, no de un campo aparte.
                # `whatsapp_phone_number` no es un campo válido aquí y provoca
                # "Invalid parameter". El número se usa en el link del creativo.
                "promoted_object": json.dumps({
                    "page_id": context["page_id"],
                }),
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
            endpoint = f"{acc.external_account_id}/adsets"
            result = await limiter.call_api(
                endpoint, payload, method="POST", access_token=acc.get_access_token()
            )
            _raise_if_meta_error(result)

            adset_id = (
                f"mock_adset_{int(datetime.now().timestamp())}"
                if settings.simulate_meta_writes_for(acc.id)
                else result.get("id")
            )
            if not adset_id:
                raise Exception(f"Meta no devolvió id de adset: {result}")

            context["adset_id"] = adset_id
            await _merge_draft_result(db, context["draft_id"], adset_id=adset_id)
            return context

    return run_async(_impl())


@celery_app.task(**_RETRY_KWARGS)
def task_create_creative(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 4: Crear Creativo — POST real a `/{act}/adcreatives`.
    """
    logger.info(f"Task 4 (Create Creative) - context: {context}")

    async def _impl():
        async with async_session() as db:
            acc = await _get_ad_account(db, context["ad_account_id"])

            brand = _brand_code(acc)
            creative_name = f"{brand}_CREATIVE_{context['product_name']}_{datetime.now().strftime('%b%Y').upper()}"
            spec = CreativeBuilder.build_creative_payload(
                name=creative_name,
                page_id=context["page_id"],
                copy_text=context["copy_text"],
                image_hash=context.get("image_hash"),
                video_id=context.get("video_id"),
                thumbnail_url=context.get("thumbnail_url"),
                whatsapp_number=context.get("whatsapp_number"),
            )

            payload = {
                "name": creative_name,
                "object_story_spec": json.dumps(spec["object_story_spec"]),
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
            endpoint = f"{acc.external_account_id}/adcreatives"
            result = await limiter.call_api(
                endpoint, payload, method="POST", access_token=acc.get_access_token()
            )
            _raise_if_meta_error(result)

            creative_id = (
                f"mock_creative_{int(datetime.now().timestamp())}"
                if settings.simulate_meta_writes_for(acc.id)
                else result.get("id")
            )
            if not creative_id:
                raise Exception(f"Meta no devolvió id de creativo: {result}")

            context["creative_id"] = creative_id
            await _merge_draft_result(db, context["draft_id"], creative_id=creative_id)
            return context

    return run_async(_impl())


@celery_app.task(**_RETRY_KWARGS)
def task_create_ad(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paso 5: Crear Ad y finalizar — POST real a `/{act}/ads`.
    """
    logger.info(f"Task 5 (Create Ad) - context: {context}")

    async def _impl():
        async with async_session() as db:
            acc = await _get_ad_account(db, context["ad_account_id"])
            rules = await _get_campaign_rules(db, acc)

            brand = _brand_code(acc)
            ad_name = f"{brand}_AD_{context['product_name']}_{datetime.now().strftime('%b%Y').upper()}"
            payload = {
                "name": ad_name,
                "adset_id": context["adset_id"],
                "creative": json.dumps({"creative_id": context["creative_id"]}),
                # Regla de oro del playbook (#3): todo se crea PAUSED.
                "status": rules["default_status"],
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
            endpoint = f"{acc.external_account_id}/ads"
            result = await limiter.call_api(
                endpoint, payload, method="POST", access_token=acc.get_access_token()
            )
            _raise_if_meta_error(result)

            ad_id = (
                f"mock_ad_{int(datetime.now().timestamp())}"
                if settings.simulate_meta_writes_for(acc.id)
                else result.get("id")
            )
            if not ad_id:
                raise Exception(f"Meta no devolvió id de anuncio: {result}")

            context["ad_id"] = ad_id
            context["status"] = "COMPLETED"
            await _merge_draft_result(db, context["draft_id"], status="CREATED", ad_id=ad_id)

            # A-1: si la Saga fue despachada por una AIAction (creación
            # aprobada por la IA, no el formulario manual), cerrar el ciclo de
            # esa acción también — antes solo el CampaignDraft se enteraba.
            action_id = context.get("action_id")
            if action_id:
                from app.models.ai_action import AIAction
                action = await db.get(AIAction, action_id)
                if action:
                    action.status = "COMPLETED"
                    action.execution_result = {
                        "campaign_id": context["campaign_id"],
                        "adset_id": context["adset_id"],
                        "creative_id": context["creative_id"],
                        "ad_id": ad_id,
                        "simulated": settings.simulate_meta_writes_for(acc.id),
                    }
                    db.add(action)
                    await db.commit()

            logger.info(f"Campaign creation Saga completed successfully: {context}")

            from app.services.notification_service import NotificationService
            notifier = NotificationService()
            notifier.notify_job_status(
                context.get("tenant_id", "unknown-tenant"),
                context["draft_id"],
                "READY",
                event_type="campaign_creation",
            )

            return context

    return run_async(_impl())


@celery_app.task
def task_handle_chain_error(request, exc, traceback, draft_id, tenant_id: str = "unknown-tenant", action_id: str | None = None):
    """
    Callback de error para el chain. Si alguna tarea falla, cae aquí.
    Marca el CampaignDraft como FAILED con el mensaje real (antes no se
    persistía nada; el frontend no tenía forma de enterarse del motivo).
    """
    logger.error(f"Saga Failed for draft_id {draft_id}: {exc}")

    async def _impl():
        async with async_session() as db:
            await _merge_draft_result(db, draft_id, status="FAILED", error_message=str(exc))

            # A-1: si la Saga venía de una AIAction, también cerrarla como FAILED
            # (antes la acción se quedaba EXECUTING para siempre).
            if action_id:
                from app.models.ai_action import AIAction
                action = await db.get(AIAction, action_id)
                if action:
                    action.status = "FAILED"
                    action.execution_result = {
                        "error": str(exc),
                        "timestamp": datetime.now().isoformat(),
                    }
                    db.add(action)
                    await db.commit()

    run_async(_impl())

    from app.services.notification_service import NotificationService
    notifier = NotificationService()
    notifier.notify_job_status(tenant_id, draft_id, "FAILED", event_type="campaign_creation")
