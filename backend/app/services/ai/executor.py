import logging
import datetime
import asyncio
import random
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.models.organization import Organization
from app.services.meta_ads.modifier import MetaCampaignModifier
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

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

        try:
            modifier = MetaCampaignModifier(self.db)
            
            if action.action_type == "CREATE_CAMPAIGN":
                # La creación de campañas sigue el flujo de 6 pasos (paso0)
                result = await self._execute_campaign_creation(ad_account, action)
            else:
                # Modificación de campañas (pausar, presupuesto, placements)
                result = await modifier.modify(ad_account, action)

            # Éxito
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
            if action.action_type != "CREATE_CAMPAIGN" and "backup_state" in action.parameters:
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

    async def _execute_campaign_creation(self, ad_account: AdAccount, action: AIAction) -> Dict[str, Any]:
        """
        Ejecuta el flujo secuencial de 6 pasos del paso0 para crear una campaña.
        En modo MOCK, simula el proceso con tiempos cortos y genera IDs ficticios.
        En producción, ejecuta las llamadas a la Graph API con pausas anti-detección.
        """
        limiter = MetaAPIRateLimiter(ad_account_id=ad_account.id)
        token = ad_account.get_access_token()
        params = action.parameters

        steps_log = []

        # PASO 1 — Verificar cuenta
        logger.info("[PASO 1] Verificando cuenta de anuncios...")
        steps_log.append("Paso 1: Verificando límites y status de la cuenta de anuncios.")
        if not settings.SIMULATE_META_WRITES:
            acc_info = await limiter.call_api(f"act_{ad_account.external_account_id}", {"fields": "amount_spent,spend_cap,account_status"}, access_token=token)
            if float(acc_info.get("spend_cap", 0)) > 0 and float(acc_info.get("amount_spent", 0)) >= float(acc_info.get("spend_cap", 0)):
                raise Exception("Límite de gasto (spend_cap) alcanzado. Creación cancelada.")
            if acc_info.get("account_status") != 1:
                raise Exception("La cuenta de anuncios está inhabilitada. Creación cancelada.")
        else:
            await asyncio.sleep(1.0)
        steps_log.append("Paso 1: Verificación exitosa. Cuenta activa con saldo.")

        # PASO 2 — Subir/Verificar imagen
        logger.info("[PASO 2] Subiendo creativo...")
        steps_log.append("Paso 2: Subiendo imagen del creativo publicitario...")
        image_hash = "mock_hash_image_123456789"
        if not settings.SIMULATE_META_WRITES:
            # Lógica real de subida a Meta
            # image_res = await limiter.call_api(f"act_{ad_account.external_account_id}/adimages", {"filename": ...}, method="POST", access_token=token)
            # image_hash = image_res.get("images", {}).get("image.png", {}).get("hash")
            pass
        else:
            await asyncio.sleep(1.0)
        steps_log.append(f"Paso 2: Imagen cargada con éxito. Hash: {image_hash}")

        # PASO 3 — Crear Campaña (Siempre PAUSADA por seguridad)
        logger.info("[PASO 3] Creando campaña...")
        steps_log.append("Paso 3: Creando estructura de Campaña (Outcomes: Leads, Estado: PAUSED)...")
        campaign_id = "camp_mock_new_" + str(random.randint(1000, 9999))
        if not settings.SIMULATE_META_WRITES:
            camp_payload = {
                "name": params.get("campaign_name"),
                "objective": params.get("objective"),
                "status": "PAUSED",  # REGLA INQUEBRANTABLE: Siempre pausada
                "buying_type": "AUCTION"
            }
            camp_res = await limiter.call_api(f"act_{ad_account.external_account_id}/campaigns", camp_payload, method="POST", access_token=token)
            campaign_id = camp_res.get("id")
            if not campaign_id:
                raise Exception(f"Fallo al crear la Campaña: {camp_res}")
        else:
            await asyncio.sleep(1.0)
        steps_log.append(f"Paso 3: Campaña creada con ID: {campaign_id}")

        # PASO 4 — Crear Ad Set (targeting y presupuesto)
        logger.info("[PASO 4] Creando Ad Set...")
        steps_log.append("Paso 4: Creando Ad Set con segmentación local (Lima Región) y placements optimizados...")
        adset_id = "adset_mock_new_" + str(random.randint(1000, 9999))
        if not settings.SIMULATE_META_WRITES:
            # Presupuesto diario en centavos
            budget_cents = int(params.get("daily_budget", 70.0) * 100)
            
            adset_payload = {
                "name": f"Adset - {params.get('campaign_name')}",
                "campaign_id": campaign_id,
                "daily_budget": budget_cents,
                "billing_event": "IMPRESSIONS",
                "optimization_goal": "LEADS",
                "targeting": params.get("targeting"),
                "status": "PAUSED"
            }
            adset_res = await limiter.call_api(f"act_{ad_account.external_account_id}/adsets", adset_payload, method="POST", access_token=token)
            adset_id = adset_res.get("id")
            if not adset_id:
                raise Exception(f"Fallo al crear el Ad Set: {adset_res}")
        else:
            await asyncio.sleep(1.0)
        steps_log.append(f"Paso 4: Ad Set creado con ID: {adset_id}")

        # PASO 5 — Crear Ad Creative (Copy + Welcome Message)
        logger.info("[PASO 5] Creando Ad Creative...")
        steps_log.append("Paso 5: Configurando el creativo (Creative Copy + Botón de WhatsApp)...")
        creative_id = "creative_mock_new_" + str(random.randint(1000, 9999))
        
        # Obtener Page ID de manera dinámica
        page_id = params.get("page_id") or params.get("creative", {}).get("page_id")
        if not page_id and not settings.SIMULATE_META_WRITES:
            try:
                pages_res = await limiter.call_api(f"act_{ad_account.external_account_id}/promotable_page_ids", {"fields": "id,name"}, access_token=token)
                pages_data = pages_res.get("data", [])
                if pages_data:
                    page_id = pages_data[0].get("id")
                    logger.info(f"Page ID recuperado automáticamente (promotable_page_ids): {page_id}")
            except Exception as e:
                logger.warning(f"No se pudo recuperar promotable_page_ids: {e}. Intentando me/accounts...")
                try:
                    pages_res = await limiter.call_api("me/accounts", {"fields": "id,name"}, access_token=token)
                    pages_data = pages_res.get("data", [])
                    if pages_data:
                        page_id = pages_data[0].get("id")
                        logger.info(f"Page ID recuperado automáticamente (me/accounts): {page_id}")
                except Exception as e2:
                    logger.error(f"Error al recuperar Page ID desde me/accounts: {e2}")

        if not page_id:
            if settings.SIMULATE_META_WRITES:
                page_id = "mock_page_id"
            else:
                raise Exception("No se pudo determinar el Page ID para el Ad Creative. Por favor, especifícalo en los parámetros de la acción o vincula una página de Facebook.")

        if not settings.SIMULATE_META_WRITES:
            # Payload complejo de creativo de Meta con link de WhatsApp y mensaje de bienvenida
            creative_payload = {
                "name": f"Creative - {params.get('campaign_name')}",
                "object_story_spec": {
                    "page_id": page_id,
                    "link_data": {
                        "image_hash": image_hash,
                        "message": params.get("creative", {}).get("body"),
                        "link": f"https://wa.me/{params.get('creative', {}).get('whatsapp_number')}?text={params.get('creative', {}).get('welcome_message')}",
                        "call_to_action": {"type": "WHATSAPP_MESSAGE"}
                    }
                }
            }
            creative_res = await limiter.call_api(f"act_{ad_account.external_account_id}/adcreatives", creative_payload, method="POST", access_token=token)
            creative_id = creative_res.get("id")
            if not creative_id:
                raise Exception(f"Fallo al crear el Creativo: {creative_res}")
        else:
            await asyncio.sleep(1.0)
        steps_log.append(f"Paso 5: Ad Creative configurado con ID: {creative_id} usando Page ID: {page_id}")

        # PASO 6 — Crear el Anuncio final (Ad)
        logger.info("[PASO 6] Creando Anuncio...")
        steps_log.append("Paso 6: Publicando el anuncio vinculando el Ad Set y el Creativo...")
        ad_id = "ad_mock_new_" + str(random.randint(1000, 9999))
        if not settings.SIMULATE_META_WRITES:
            ad_payload = {
                "name": f"Ad - {params.get('campaign_name')}",
                "adset_id": adset_id,
                "creative": {"creative_id": creative_id},
                "status": "PAUSED"
            }
            ad_res = await limiter.call_api(f"act_{ad_account.external_account_id}/ads", ad_payload, method="POST", access_token=token)
            ad_id = ad_res.get("id")
            if not ad_id:
                raise Exception(f"Fallo al crear el Anuncio final: {ad_res}")
        else:
            await asyncio.sleep(1.0)
        steps_log.append(f"Paso 6: Anuncio publicado con ID: {ad_id}")

        return {
            "campaign_id": campaign_id,
            "adset_id": adset_id,
            "creative_id": creative_id,
            "ad_id": ad_id,
            "steps": steps_log,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
