import json
import logging
from typing import Dict, Any
from datetime import datetime, timedelta

from app.workers.celery_app import celery_app
from app.workers.tasks import run_async
from app.database import async_session
from app.config import settings
from app.models.ad_account import AdAccount
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES
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


def _raise_if_meta_error(result: Dict[str, Any]) -> None:
    """Si `result` es una respuesta de error de Meta, la clasifica y la lanza
    (transitoria => Celery reintenta vía autoretry_for; permanente => corta el
    chain y cae en `task_handle_chain_error`)."""
    if not isinstance(result, dict) or "error" in result or "ERROR" in result:
        raise classify_meta_error(Exception(json.dumps(result)))


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
            image_hash = context.get("image_hash")
            if not image_hash:
                limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
                repo = MetaAssetRepository(limiter)
                image_hash = await repo.get_or_upload_image(
                    ad_account_external_id=acc.external_account_id,
                    access_token=acc.get_access_token(),
                    image_url=context.get("image_url"),
                    image_hash=None,
                )
            context["image_hash"] = image_hash
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

            product_name = context["product_name"]
            zone = context["zone"]
            month_year = datetime.now().strftime("%b%Y").upper()
            campaign_name = f"MTV_MSJ_{product_name}_{zone}_{month_year}".replace(" ", "")

            payload = {
                "name": campaign_name,
                "objective": TENANT_CAMPAIGN_RULES["objective"],
                "buying_type": "AUCTION",
                "status": TENANT_CAMPAIGN_RULES["default_status"],
                "special_ad_categories": "[]",
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
            endpoint = f"{acc.external_account_id}/campaigns"
            result = await limiter.call_api(
                endpoint, payload, method="POST", access_token=acc.get_access_token()
            )
            _raise_if_meta_error(result)

            campaign_id = (
                f"mock_camp_{int(datetime.now().timestamp())}"
                if settings.SIMULATE_META_WRITES
                else result.get("id")
            )
            if not campaign_id:
                raise Exception(f"Meta no devolvió id de campaña: {result}")

            context["campaign_id"] = campaign_id
            context["campaign_name"] = campaign_name
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
            campaign_id = context["campaign_id"]

            start_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0000")
            end_time = (datetime.now() + timedelta(days=context["days"])).strftime("%Y-%m-%dT%H:%M:%S+0000")

            payload = {
                "name": f"{context['campaign_name']}_AS01",
                "campaign_id": campaign_id,
                "optimization_goal": TENANT_CAMPAIGN_RULES["optimization_goal"],
                "destination_type": TENANT_CAMPAIGN_RULES["destination_type"],
                "billing_event": "IMPRESSIONS",
                "bid_strategy": TENANT_CAMPAIGN_RULES["bid_strategy"],
                "lifetime_budget": int(context["budget"] * 100),  # Meta espera centavos
                "start_time": start_time,
                "end_time": end_time,
                "status": "ACTIVE",  # TODO (Fase 2 del plan): debe ser PAUSED
                "targeting": json.dumps({
                    "geo_locations": {"regions": [{"key": TENANT_CAMPAIGN_RULES["default_geo_region_key"]}]},
                    "age_min": 25,
                    "age_max": 65,
                    "publisher_platforms": ["facebook"],
                    "facebook_positions": ["feed", "story"] + TENANT_CAMPAIGN_RULES["required_placements"],
                    "targeting_automation": {"advantage_audience": 1},
                }),
                "promoted_object": json.dumps({
                    "page_id": context["page_id"],
                    "whatsapp_phone_number": context["whatsapp_number"],
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
                if settings.SIMULATE_META_WRITES
                else result.get("id")
            )
            if not adset_id:
                raise Exception(f"Meta no devolvió id de adset: {result}")

            context["adset_id"] = adset_id
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

            creative_name = f"MTV_CREATIVE_{context['product_name']}_{datetime.now().strftime('%b%Y').upper()}"
            spec = CreativeBuilder.build_creative_payload(
                name=creative_name,
                page_id=context["page_id"],
                image_hash=context["image_hash"],
                copy_text=context["copy_text"],
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
                if settings.SIMULATE_META_WRITES
                else result.get("id")
            )
            if not creative_id:
                raise Exception(f"Meta no devolvió id de creativo: {result}")

            context["creative_id"] = creative_id
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

            ad_name = f"MTV_AD_{context['product_name']}{context['zone']}_{datetime.now().strftime('%b%Y').upper()}"
            payload = {
                "name": ad_name,
                "adset_id": context["adset_id"],
                "creative": json.dumps({"creative_id": context["creative_id"]}),
                "status": "ACTIVE",  # TODO (Fase 2 del plan): debe ser PAUSED
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id)
            endpoint = f"{acc.external_account_id}/ads"
            result = await limiter.call_api(
                endpoint, payload, method="POST", access_token=acc.get_access_token()
            )
            _raise_if_meta_error(result)

            ad_id = (
                f"mock_ad_{int(datetime.now().timestamp())}"
                if settings.SIMULATE_META_WRITES
                else result.get("id")
            )
            if not ad_id:
                raise Exception(f"Meta no devolvió id de anuncio: {result}")

            context["ad_id"] = ad_id
            context["status"] = "COMPLETED"

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
def task_handle_chain_error(request, exc, traceback, draft_id, tenant_id: str = "unknown-tenant"):
    """
    Callback de error para el chain. Si alguna tarea falla, cae aquí.
    """
    logger.error(f"Saga Failed for draft_id {draft_id}: {exc}")
    from app.services.notification_service import NotificationService
    notifier = NotificationService()
    notifier.notify_job_status(tenant_id, draft_id, "FAILED", event_type="campaign_creation")
