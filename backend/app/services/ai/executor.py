import logging
import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from celery import chain
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.models.organization import Organization
from app.models.campaign_draft import CampaignDraft
from app.models.meta_page import MetaPage
from app.models.meta_creative import MetaCreative
from app.services.meta_ads.modifier import MetaCampaignModifier
# Import perezoso de app.workers.creation_tasks (ver abajo): ese módulo importa
# app.workers.tasks, que a su vez importa AIExecutor — un import a nivel de
# módulo aquí crearía un ciclo (tasks -> executor -> creation_tasks -> tasks).

logger = logging.getLogger("lejovista.executor")

class AIExecutor:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute_action(self, ad_account: AdAccount, action: AIAction) -> AIAction:
        """
        Ejecuta una acción aprobada de forma autónoma respetando los protocolos de seguridad.
        """
        if action.status != "APPROVED":
            raise ValueError(f"La acción no puede ejecutarse porque está en estado '{action.status}' (requiere 'APPROVED').")

        # 1. Verificar límites de seguridad financiera (Presupuesto mensual del tenant)
        # Buscar la organización
        org = await self.db.get(Organization, action.organization_id)
        if not org:
            raise Exception("Organización no encontrada.")

        if org.status != "ACTIVE":
            action.status = "FAILED"
            action.execution_result = {"error": "La organización está suspendida o inactiva."}
            await self.db.commit()
            return action

        # Marcar como ejecutando
        action.status = "EXECUTING"
        action.executed_at = datetime.datetime.utcnow()
        await self.db.commit()

        if action.action_type == "CREATE_CAMPAIGN":
            # A-1: unificado con el único camino real de creación (la Saga de
            # creation_tasks) — antes existía un flujo inline duplicado
            # (`_execute_campaign_creation`) con su propio bug del image_hash
            # mock que nunca llegaba a Meta. La acción se queda en EXECUTING;
            # el chain la marca COMPLETED/FAILED al terminar (igual que las
            # campañas creadas manualmente desde /campaigns/).
            try:
                return await self._dispatch_campaign_creation_saga(ad_account, action)
            except Exception as e:
                logger.exception(f"No se pudo despachar la Saga de creación para la acción {action.id}")
                action.status = "FAILED"
                action.execution_result = {
                    "error": str(e),
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                await self.db.commit()
                return action

        try:
            modifier = MetaCampaignModifier(self.db)
            result = await modifier.modify(ad_account, action)

            # Éxito. Marcamos si fue una escritura real en Meta o una simulación
            # (write-back desactivado), para que el frontend no dé por hecho que
            # la campaña se pausó/modificó realmente (P-1).
            if isinstance(result, dict):
                result["simulated"] = settings.simulate_meta_writes_for(ad_account.id)
            action.status = "COMPLETED"
            action.execution_result = result

        except Exception as e:
            logger.exception(f"Falla crítica en la ejecución de la acción {action.id}")
            action.status = "FAILED"
            action.execution_result = {
                "error": str(e),
                "timestamp": datetime.datetime.utcnow().isoformat()
            }

            # En producción, si hay error y es modificación, intentar rollback inmediato
            if "backup_state" in action.parameters:
                logger.warning(f"Iniciando rollback automático para acción {action.id}...")
                try:
                    modifier = MetaCampaignModifier(self.db)
                    await modifier.rollback(ad_account, action)
                    action.execution_result["rollback"] = "Rollback ejecutado con éxito por seguridad."
                except Exception as rollback_err:
                    logger.error(f"Fallo el rollback automático: {rollback_err}")
                    action.execution_result["rollback_error"] = str(rollback_err)

        await self.db.commit()
        return action

    async def _dispatch_campaign_creation_saga(self, ad_account: AdAccount, action: AIAction) -> AIAction:
        """A-1: crea el `CampaignDraft` desde `action.parameters` y despacha el
        MISMO chain que usa la creación manual (`POST /campaigns/`).

        Dos datos que el planner no puede inventar con seguridad (regla AI-1):
        la página/WhatsApp de destino y el creativo (imagen) del anuncio. Si no
        se resuelven sin ambigüedad, la acción queda en `NEEDS_INPUT` — visible
        en `/actions` para que un humano la complete — en vez de adivinar.
        """
        from app.workers.creation_tasks import (
            task_upload_image, task_create_campaign, task_create_adset,
            task_create_creative, task_create_ad, task_handle_chain_error,
        )

        params = action.parameters or {}

        # 1º: la página predeterminada configurada para esta cuenta (Settings →
        # "Páginas y WhatsApp"). 2º fallback: la regla original — exactamente 1
        # página activa con WhatsApp en la org. Si ninguna resuelve, NEEDS_INPUT.
        page = None
        if ad_account.default_page_id:
            default_page = await self.db.get(MetaPage, ad_account.default_page_id)
            if default_page and default_page.status == "ACTIVE" and default_page.whatsapp_number:
                page = default_page
            elif default_page and not default_page.whatsapp_number:
                return await self._mark_needs_input(
                    action,
                    f"La página predeterminada de la cuenta ('{default_page.name}') no tiene un número "
                    "de WhatsApp conectado. Agrégalo en Configuración → Páginas y WhatsApp."
                )

        if page is None:
            pages = (await self.db.execute(
                select(MetaPage).where(
                    MetaPage.organization_id == action.organization_id,
                    MetaPage.status == "ACTIVE",
                )
            )).scalars().all()
            pages_with_whatsapp = [p for p in pages if p.whatsapp_number]
            if len(pages_with_whatsapp) != 1:
                return await self._mark_needs_input(
                    action,
                    "Se requiere exactamente 1 página de Facebook activa con WhatsApp conectado "
                    f"para resolver el destino sin ambigüedad (se encontraron {len(pages_with_whatsapp)}). "
                    "Configura una página predeterminada para la cuenta en Configuración → Páginas y WhatsApp."
                )
            page = pages_with_whatsapp[0]

        creative = (await self.db.execute(
            select(MetaCreative)
            .where(MetaCreative.ad_account_id == ad_account.id)
            .order_by(MetaCreative.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not creative:
            return await self._mark_needs_input(
                action,
                "No hay ningún creativo (imagen) sincronizado en esta cuenta para reutilizar en el anuncio."
            )

        creative_params = params.get("creative") or {}
        copy_text = "\n\n".join(
            part for part in [
                creative_params.get("headline"),
                creative_params.get("body"),
                creative_params.get("description"),
            ] if part
        )
        product_name = params.get("product_name") or "Producto"

        # F3: mismo molde de nombres que la creación manual — esta ruta también
        # debe producir campañas que se vean "de la casa".
        from app.services.campaign_suggester import CampaignSuggester

        suggested_campaign_name = (
            await CampaignSuggester(self.db).suggest(ad_account, product_name=product_name)
        ).get("suggested_campaign_name")

        draft = CampaignDraft(
            organization_id=action.organization_id,
            name=product_name,
            payload={
                "source": "ai_action",
                "action_id": action.id,
                "ad_account_id": ad_account.id,
                "page_id": page.external_page_id,
                "product_name": product_name,
                "budget": params.get("daily_budget"),
                "days": params.get("duration_days"),
                "whatsapp_number": page.whatsapp_number,
                "image_hash": creative.image_hash,
                "copy_text": copy_text,
                "excluded_placements": params.get("excluded_placements") or [],
            },
            status="PENDING_CREATION",
        )
        self.db.add(draft)
        await self.db.commit()
        draft_id = draft.id

        initial_context = {
            "draft_id": draft_id,
            "tenant_id": action.organization_id,
            "action_id": action.id,
            "ad_account_id": ad_account.id,
            "page_id": page.external_page_id,
            "product_name": product_name,
            "budget": params.get("daily_budget"),
            "days": params.get("duration_days"),
            "whatsapp_number": page.whatsapp_number,
            "image_url": None,
            "image_hash": creative.image_hash,
            "image_path": None,
            "video_id": None,
            "thumbnail_url": None,
            "copy_text": copy_text,
            # F5: el creativo ya sabe colocarlos en link_data.name/description.
            # Siguen además dentro de `copy_text` para no cambiar el cuerpo del
            # anuncio que esta ruta venía produciendo.
            "headline": creative_params.get("headline"),
            "description": creative_params.get("description"),
            # F3: nombre con la nomenclatura real de la cuenta.
            "campaign_name": suggested_campaign_name,
            "best_age_range": params.get("best_age_range"),
            "best_device": params.get("best_device"),
            "excluded_placements": params.get("excluded_placements") or [],
        }

        creation_chain = chain(
            task_upload_image.s(initial_context),
            task_create_campaign.s().set(countdown=40),
            task_create_adset.s().set(countdown=40),
            task_create_creative.s().set(countdown=40),
            task_create_ad.s().set(countdown=40),
        ).on_error(task_handle_chain_error.s(draft_id, action.organization_id, action.id))
        creation_chain.apply_async()

        logger.info(f"Saga de creación despachada para la acción {action.id} (draft {draft_id}).")
        return action

    async def _mark_needs_input(self, action: AIAction, reason: str) -> AIAction:
        action.status = "NEEDS_INPUT"
        action.execution_result = {"error": reason, "needs_input": True}
        self.db.add(action)
        await self.db.commit()
        logger.warning(f"Acción {action.id} requiere entrada humana: {reason}")
        return action
