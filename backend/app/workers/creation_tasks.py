import json
import logging
from typing import Any, Dict, Optional
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
from app.services.meta_ads.exceptions import (
    PermanentMetaError, TransientMetaError, classify_meta_error,
)
from app.models.campaign_proposal import WRITE_VERIFYING
from app.services.meta_reconciliation import (
    ReconciliacionAmbigua, buscar_entidad_de_la_intencion,
)
from app.services.proposal_versioning import (
    confirmar_intencion, marcar_verificando, registrar_intencion,
)

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


async def _get_page_instagram_id(db, acc: AdAccount, external_page_id: str) -> str | None:
    """Cuenta de Instagram conectada a la página, si el sync la descubrió.

    Devuelve None si la página no está en BD o no tiene IG conectado — el
    creativo simplemente se crea sin `instagram_user_id` y Meta usa la página
    de Facebook como identidad en IG.
    """
    from app.models.meta_page import MetaPage
    from sqlalchemy import select

    from app.services.asset_scope import page_ids_of_accounts

    row = (await db.execute(select(MetaPage).where(
        MetaPage.organization_id == acc.organization_id,
        MetaPage.external_page_id == str(external_page_id),
        # Acotado a la cuenta: si la página es de otro cliente, no se le presta
        # su identidad de Instagram al creativo.
        MetaPage.id.in_(page_ids_of_accounts([acc.id])),
    ))).scalar_one_or_none()
    return row.instagram_actor_id if row else None



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


async def _rollback_campaign(db, draft_id: str) -> None:
    """Compensación de la Saga: borra en Meta la campaña ya creada cuando un paso
    posterior falla.

    Sin esto, cada intento fallido dejaba una campaña VACÍA (sin adset ni
    anuncio) y pausada en la cuenta del cliente — 3 huérfanas en una sola sesión
    de depuración el 2026-07-17, borradas a mano. La Saga escribe en 4 pasos
    encadenados pero no tenía la pata de compensación que el patrón exige.

    No borra si: la campaña nunca se creó, es simulada (`mock_*`), o la Saga
    llegó a crear el anuncio (ahí terminó bien; no hay nada que compensar).
    Best-effort: si el borrado falla, se registra en el draft (`rollback_error`)
    en vez de enmascarar el error original de la Saga.
    """
    draft = await db.get(CampaignDraft, draft_id)
    if not draft:
        return

    payload = dict(draft.payload or {})
    result = dict(payload.get("result", {}))
    campaign_id = result.get("campaign_id")
    ad_account_id = payload.get("ad_account_id")

    if not campaign_id or not ad_account_id:
        return  # la Saga no llegó al paso 2: no hay nada creado en Meta
    if str(campaign_id).startswith("mock_"):
        return  # simulada: no existe en Meta
    if result.get("ad_id"):
        return  # la Saga completó: no compensar

    acc = await db.get(AdAccount, ad_account_id)
    if not acc:
        logger.warning("Rollback: AdAccount %s no encontrada; no se borra la campaña %s",
                       ad_account_id, campaign_id)
        return

    try:
        limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
        res = await limiter.call_api(
            str(campaign_id), {}, method="DELETE", access_token=acc.get_access_token()
        )
        if isinstance(res, dict) and ("error" in res or "ERROR" in res):
            raise Exception(json.dumps(res))
        logger.info("Rollback: campaña huérfana %s borrada en Meta tras fallar la Saga.", campaign_id)
        await _merge_draft_result(db, draft_id, rolled_back_campaign_id=campaign_id)
    except Exception as exc:
        # No re-lanzar: el error que importa es el que tumbó la Saga. Este queda
        # anotado para que se pueda limpiar a mano (mismo criterio que
        # `rollback_error` en AIExecutor).
        logger.exception("Rollback: no se pudo borrar la campaña %s en Meta", campaign_id)
        await _merge_draft_result(db, draft_id, rollback_error=str(exc))


# --------------------------------------------------------------------------
# Idempotencia y reconciliación (spec 03)
# --------------------------------------------------------------------------

async def _escritura_idempotente(
    db,
    context: Dict[str, Any],
    *,
    operacion: str,
    acc,
    correlation_tag: Optional[str],
    emitir,
):
    """Envuelve una escritura real hacia Meta con su registro de intención.

    El orden importa y es todo el mecanismo:

    1. Se registra la INTENCIÓN antes del POST. Si Meta crea el objeto y la
       respuesta se pierde, queda constancia de qué se intentó, en qué cuenta y
       bajo qué autorización — sin esto la entidad queda huérfana y sin rastro.
    2. Si esa intención ya está CONFIRMADA, se devuelve el identificador
       guardado y **no se escribe**. Eso convierte un reintento de Celery, un
       doble clic o una reejecución en una recuperación del resultado anterior.
    3. Si estaba en VERIFYING, primero se reconcilia contra Meta. Repetir el POST
       a ciegas desde ahí es exactamente lo que crea la campaña duplicada.
    4. Una respuesta incierta deja la intención en VERIFYING y **no** se
       reintenta: se levanta un error permanente para que la Saga se detenga y
       diga por qué, en vez de insistir.

    `emitir()` hace la llamada y devuelve el id de Meta.
    """
    approval_id = context.get("approval_id")
    if not approval_id:
        # Sagas despachadas antes de la spec 03 (o por caminos que todavía no
        # pasan por la autorización). Se emite sin registro, que es el
        # comportamiento anterior — degradar a "sin idempotencia" es peor que
        # romper una creación en vuelo durante el despliegue.
        logger.warning(
            "Saga sin approval_id para %s: se emite sin registro de intención.", operacion,
        )
        return await emitir()

    intencion, ya_confirmada = await registrar_intencion(
        db,
        approval_id=approval_id,
        operacion=operacion,
        organization_id=context["tenant_id"],
        ad_account_id=acc.id,
        draft_id=context.get("draft_id"),
        correlation_tag=correlation_tag,
    )
    await db.commit()

    if ya_confirmada:
        logger.info(
            "%s ya estaba confirmada (%s); se reutiliza %s sin volver a escribir.",
            operacion, intencion.idempotency_key, intencion.external_id,
        )
        return intencion.external_id

    if intencion.status == WRITE_VERIFYING:
        try:
            encontrado = await buscar_entidad_de_la_intencion(db, intencion)
        except ReconciliacionAmbigua as e:
            await db.commit()
            raise PermanentMetaError(str(e)) from e
        if encontrado:
            await confirmar_intencion(db, intencion, encontrado)
            await db.commit()
            logger.info(
                "Reconciliación: %s ya existía en Meta como %s; se adopta.",
                operacion, encontrado,
            )
            return encontrado
        logger.info(
            "Reconciliación: %s no existe en Meta; el reintento es seguro.", operacion,
        )

    try:
        external_id = await emitir()
    except (TransientMetaError, PermanentMetaError):
        # Errores clasificados: Meta respondió y se sabe qué pasó. La intención
        # queda PENDING y el reintento (si lo hay) la reutiliza.
        raise
    except Exception as e:
        # Timeout, corte de red, respuesta ilegible: NO se sabe si la escritura
        # ocurrió. Este es el caso que la historia 46 nombra.
        await marcar_verificando(db, intencion, str(e))
        await db.commit()
        raise PermanentMetaError(
            f"Respuesta incierta de Meta al ejecutar {operacion}: la escritura "
            f"quedó en verificación y no se reintenta a ciegas. Causa: {e}"
        ) from e

    await confirmar_intencion(db, intencion, external_id)
    await db.commit()
    return external_id


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

            # Video subido a la app (F7): se sube a /advideos, se espera a que
            # Meta lo procese y se toma la miniatura que genera. Devuelve las
            # dos cosas porque `video_data` exige `image_url` sí o sí.
            if context.get("video_path"):
                limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
                repo = MetaAssetRepository(limiter)
                video_id, thumbnail_url = await repo.get_or_upload_video(
                    ad_account_external_id=acc.external_account_id,
                    access_token=acc.get_access_token(),
                    video_path=context["video_path"],
                    thumbnail_url=context.get("thumbnail_url"),
                )
                context["video_id"] = video_id
                context["thumbnail_url"] = thumbnail_url
                await _merge_draft_result(
                    db, context["draft_id"],
                    video_id=video_id, thumbnail_url=thumbnail_url,
                )
                return context

            image_hash = context.get("image_hash")
            if not image_hash:
                limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
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
            # F3: el nombre lo calcula el suggester con el molde real de la
            # cuenta y viaja en el contexto, para que el que se muestra en el
            # formulario sea EXACTAMENTE el que se crea. Si no vino (Sagas
            # despachadas por otras vías), se cae a la plantilla de siempre.
            campaign_name = context.get("campaign_name")
            if not campaign_name:
                month_year = datetime.now().strftime("%b%Y").upper()
                brand = _brand_code(acc)
                # El token de tipo sale de las reglas de la organización: estaba
                # clavado en "MSJ" porque la app solo sabía hacer campañas de
                # WhatsApp, y el tipo ES la plantilla de campaña.
                tipo = rules.get("campaign_name_type_token") or "MSJ"
                campaign_name = f"{brand}_{tipo}_{product_name}_{month_year}".replace(" ", "")

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

            # La campaña se crea PAUSED siempre. Es una invariante, no una
            # opción de configuración: una versión que pidiera crearla activa se
            # rechaza en validación, y acá se vuelve a comprobar porque este es
            # el punto donde el estado sale hacia Meta.
            if str(payload.get("status", "")).upper() != "PAUSED":
                raise PermanentMetaError(
                    f"La campaña se intentó crear en estado {payload.get('status')!r}. "
                    f"Toda campaña se crea PAUSED (spec 03)."
                )

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
            endpoint = f"{acc.external_account_id}/campaigns"

            async def _emitir_campana():
                result = await limiter.call_api(
                    endpoint, payload, method="POST", access_token=acc.get_access_token()
                )
                _raise_if_meta_error(result)
                nuevo = (
                    f"mock_camp_{int(datetime.now().timestamp())}"
                    if settings.simulate_meta_writes_for(acc.id, acc.external_account_id)
                    else result.get("id")
                )
                if not nuevo:
                    raise Exception(f"Meta no devolvió id de campaña: {result}")
                return nuevo

            campaign_id = await _escritura_idempotente(
                db, context, operacion="create_campaign", acc=acc,
                # La huella con la que se reconoce esta campaña si la respuesta
                # se pierde: su nombre, que es determinista.
                correlation_tag=campaign_name,
                emitir=_emitir_campana,
            )

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

            # Presupuesto diario o total (Meta espera centavos en ambos, y son
            # mutuamente excluyentes: mandar los dos rechaza el adset). Con
            # DAILY se conservan start/end_time, que acotan la duración.
            budget_cents = int(round(context["budget"] * 100))
            if context.get("budget_type") == "DAILY":
                payload["daily_budget"] = budget_cents
            else:
                payload["lifetime_budget"] = budget_cents

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
            endpoint = f"{acc.external_account_id}/adsets"

            async def _emitir_adset():
                result = await limiter.call_api(
                    endpoint, payload, method="POST", access_token=acc.get_access_token()
                )
                _raise_if_meta_error(result)
                nuevo = (
                    f"mock_adset_{int(datetime.now().timestamp())}"
                    if settings.simulate_meta_writes_for(acc.id, acc.external_account_id)
                    else result.get("id")
                )
                if not nuevo:
                    raise Exception(f"Meta no devolvió id de adset: {result}")
                return nuevo

            adset_id = await _escritura_idempotente(
                db, context, operacion="create_adset", acc=acc,
                correlation_tag=payload["name"],
                emitir=_emitir_adset,
            )

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

            # Identidad de Instagram: se resuelve AQUÍ (no en el endpoint) para
            # que la cubran por igual el formulario manual y la Saga que
            # despacha el AIExecutor. El sync ya guardó el IG conectado a la
            # página en MetaPage.instagram_actor_id.
            instagram_user_id = await _get_page_instagram_id(db, acc, context["page_id"])

            spec = CreativeBuilder.build_creative_payload(
                name=creative_name,
                page_id=context["page_id"],
                copy_text=context["copy_text"],
                image_hash=context.get("image_hash"),
                video_id=context.get("video_id"),
                thumbnail_url=context.get("thumbnail_url"),
                whatsapp_number=context.get("whatsapp_number"),
                headline=context.get("headline"),
                description=context.get("description"),
                instagram_user_id=instagram_user_id,
            )

            payload = {
                "name": creative_name,
                "object_story_spec": json.dumps(spec["object_story_spec"]),
            }

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
            endpoint = f"{acc.external_account_id}/adcreatives"

            async def _emitir_creativo():
                result = await limiter.call_api(
                    endpoint, payload, method="POST", access_token=acc.get_access_token()
                )
                _raise_if_meta_error(result)
                nuevo = (
                    f"mock_creative_{int(datetime.now().timestamp())}"
                    if settings.simulate_meta_writes_for(acc.id, acc.external_account_id)
                    else result.get("id")
                )
                if not nuevo:
                    raise Exception(f"Meta no devolvió id de creativo: {result}")
                return nuevo

            # Sin `correlation_tag`: un creativo no se puede buscar por nombre de
            # la misma forma que una campaña o un adset, así que ante respuesta
            # incierta la intención se queda en verificación y hace falta una
            # persona. Decirlo es mejor que fingir una reconciliación que no
            # puede distinguir un creativo de otro.
            creative_id = await _escritura_idempotente(
                db, context, operacion="create_creative", acc=acc,
                correlation_tag=None, emitir=_emitir_creativo,
            )

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

            if str(payload.get("status", "")).upper() != "PAUSED":
                raise PermanentMetaError(
                    f"El anuncio se intentó crear en estado {payload.get('status')!r}. "
                    f"Todo se crea PAUSED (spec 03)."
                )

            limiter = MetaAPIRateLimiter(ad_account_id=acc.id, external_account_id=acc.external_account_id)
            endpoint = f"{acc.external_account_id}/ads"

            async def _emitir_anuncio():
                result = await limiter.call_api(
                    endpoint, payload, method="POST", access_token=acc.get_access_token()
                )
                _raise_if_meta_error(result)
                nuevo = (
                    f"mock_ad_{int(datetime.now().timestamp())}"
                    if settings.simulate_meta_writes_for(acc.id, acc.external_account_id)
                    else result.get("id")
                )
                if not nuevo:
                    raise Exception(f"Meta no devolvió id de anuncio: {result}")
                return nuevo

            ad_id = await _escritura_idempotente(
                db, context, operacion="create_ad", acc=acc,
                correlation_tag=ad_name, emitir=_emitir_anuncio,
            )

            context["ad_id"] = ad_id
            context["status"] = "COMPLETED"
            # `simulated` viaja hasta el frontend: hasta ahora la Saga terminaba
            # igual con escrituras simuladas y la pantalla decía "¡Campaña
            # creada!" sin nada en Meta. Que el sistema sepa la diferencia y no
            # se la cuente al usuario es lo peor de los dos mundos.
            motivo = settings.writeback_block_reason(acc.id, acc.external_account_id)
            await _merge_draft_result(
                db, context["draft_id"], status="CREATED", ad_id=ad_id,
                simulated=motivo is not None, simulated_reason=motivo,
            )

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
                        "simulated": settings.simulate_meta_writes_for(acc.id, acc.external_account_id),
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


@celery_app.task(bind=True)
def task_verify_campaign(self, context: Dict[str, Any]) -> Dict[str, Any]:
    """Paso 6: releer los cuatro niveles en Meta y reportar su estado real.

    Hasta acá, "campaña creada con éxito" significaba únicamente "Meta aceptó
    el POST". No es lo mismo: Meta acepta la creación y después marca el objeto
    con problemas de política o de configuración, y eso vive en `issues_info`.
    Sin releer, el usuario ve un cartel de éxito sobre una campaña que no va a
    entregar nunca.

    Esta tarea NO falla la Saga. Los cuatro objetos ya existen en Meta; que la
    verificación no se pueda hacer —token vencido, rate limit, red— no es
    motivo para disparar el rollback y borrarlos. Se registra lo que se pudo
    leer y se sigue.
    """
    logger.info("Task 6 (Verify) - draft %s", context.get("draft_id"))

    async def _impl():
        async with async_session() as db:
            acc = await _get_ad_account(db, context["ad_account_id"])
            limiter = MetaAPIRateLimiter(
                ad_account_id=acc.id, external_account_id=acc.external_account_id
            )
            token = acc.get_access_token()

            niveles = [
                ("campaign", context.get("campaign_id")),
                ("adset", context.get("adset_id")),
                ("ad", context.get("ad_id")),
            ]
            verificacion: Dict[str, Any] = {"niveles": {}, "problemas": []}

            for nombre, obj_id in niveles:
                if not obj_id:
                    continue
                try:
                    res = await limiter.call_api(
                        str(obj_id),
                        {"fields": "id,name,status,effective_status,issues_info"},
                        access_token=token,
                    )
                except Exception as exc:
                    logger.warning("No se pudo verificar %s %s: %s", nombre, obj_id, exc)
                    verificacion["niveles"][nombre] = {"id": obj_id, "verificado": False}
                    continue

                if not isinstance(res, dict) or "error" in res or "ERROR" in res:
                    verificacion["niveles"][nombre] = {"id": obj_id, "verificado": False}
                    continue

                issues = res.get("issues_info") or []
                verificacion["niveles"][nombre] = {
                    "id": obj_id,
                    "verificado": True,
                    "status": res.get("status"),
                    "effective_status": res.get("effective_status"),
                    "issues": issues,
                }
                for issue in issues:
                    verificacion["problemas"].append({
                        "nivel": nombre,
                        "titulo": issue.get("error_summary") or issue.get("error_message"),
                        "detalle": issue.get("error_message"),
                        "como_resolver": issue.get("confirmation_manual_action"),
                    })

            # La compuerta de seguridad, releída de Meta y no asumida: TODO
            # tiene que haber nacido PAUSED. Si algo salió ACTIVE es un
            # incidente, no una curiosidad.
            activos = [
                n for n, d in verificacion["niveles"].items()
                if d.get("verificado") and d.get("status") == "ACTIVE"
            ]
            if activos:
                verificacion["alerta_activo"] = activos
                logger.error(
                    "Objetos creados en estado ACTIVE (deberían nacer PAUSED): %s — draft %s",
                    activos, context.get("draft_id"),
                )

            await _merge_draft_result(db, context["draft_id"], verificacion=verificacion)
            context["verificacion"] = verificacion
            logger.info(
                "Verificación del draft %s: %d problema(s) reportado(s) por Meta.",
                context.get("draft_id"), len(verificacion["problemas"]),
            )
            return context

    return run_async(_impl())


@celery_app.task
def task_handle_chain_error(request, exc, traceback, draft_id, tenant_id: str = "unknown-tenant", action_id: str | None = None):
    """
    Callback de error para el chain. Si alguna tarea falla, cae aquí.
    Marca el CampaignDraft como FAILED con el mensaje real (antes no se
    persistía nada; el frontend no tenía forma de enterarse del motivo) y
    compensa la Saga borrando la campaña ya creada en Meta (`_rollback_campaign`).
    """
    logger.error(f"Saga Failed for draft_id {draft_id}: {exc}")

    async def _impl():
        async with async_session() as db:
            await _merge_draft_result(db, draft_id, status="FAILED", error_message=str(exc))

            # Compensación: si el paso 2 ya creó la campaña en Meta y un paso
            # posterior falló, borrarla para no dejarla huérfana en la cuenta.
            await _rollback_campaign(db, draft_id)

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
