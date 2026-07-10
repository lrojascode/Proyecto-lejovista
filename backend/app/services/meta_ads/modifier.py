import logging
import datetime
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

logger = logging.getLogger("lejovista.modifier")

# Acciones cuya escritura real en Meta ocurre a nivel de adset, no de campaña
# (AI-2). Debe coincidir con ADSET_LEVEL_ACTIONS en app/services/ai/planner.py.
ADSET_LEVEL_ACTIONS = {"MODIFY_BUDGET", "MODIFY_PLACEMENTS", "MODIFY_TARGETING"}


def _resolve_target_id(action: AIAction) -> str | None:
    """Elige el nodo de Meta correcto según el tipo de acción: adset para
    presupuesto/targeting/placements, campaña para pausar/reanudar."""
    if action.action_type in ADSET_LEVEL_ACTIONS:
        return action.target_adset_id or action.target_campaign_id
    return action.target_campaign_id or action.target_adset_id


class MetaCampaignModifier:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def modify(self, ad_account: AdAccount, action: AIAction) -> Dict[str, Any]:
        """
        Ejecuta la modificación especificada en la acción sobre Meta Ads.
        Guarda un snapshot del estado actual antes de modificar para permitir rollback.
        """
        limiter = MetaAPIRateLimiter(ad_account_id=ad_account.id)
        token = ad_account.get_access_token()

        target_id = _resolve_target_id(action)
        if not target_id:
            raise ValueError("Falta target_campaign_id o target_adset_id para la modificación.")

        # 1. Obtener estado actual (Backup para rollback)
        backup_state = {}
        if not settings.SIMULATE_META_WRITES:
            # En producción, hacemos un GET del estado actual
            backup_state = await limiter.call_api(
                f"{target_id}",
                {"fields": "status,daily_budget,lifetime_budget,targeting,name"},
                access_token=token
            )
            if "error" in backup_state or "ERROR" in backup_state:
                raise Exception(f"No se pudo respaldar el estado de {target_id}: {backup_state}")
        else:
            # En mock, creamos un backup simulado
            backup_state = {
                "id": target_id,
                "status": "ACTIVE",
                "daily_budget": action.parameters.get("old_budget", 50.0) * 100, # en centavos
                "targeting": {"geo_locations": {"keys": [2771]}, "publisher_platforms": ["facebook", "instagram"]}
            }

        # Guardar el estado previo en los parámetros de la acción. REASIGNAR el
        # dict (no mutarlo in-place): `parameters` es una columna JSON plana y
        # SQLAlchemy no detecta mutaciones internas — con la mutación in-place el
        # backup solo vivía en memoria y se perdía tras un reinicio del worker,
        # dejando el rollback sin datos (A-3).
        action.parameters = {**action.parameters, "backup_state": backup_state}
        self.db.add(action)
        await self.db.commit()

        # 2. Ejecutar la modificación
        action_type = action.action_type
        endpoint = f"{target_id}"
        post_data = {}

        if action_type == "MODIFY_BUDGET":
            # Meta espera el presupuesto en centavos
            new_budget_nominal = action.parameters.get("new_budget")
            if not new_budget_nominal:
                raise ValueError("Falta el parámetro 'new_budget' para modificar presupuesto.")
            
            # Regla de seguridad: presupuesto máximo de 150% del actual
            old_budget_nominal = action.parameters.get("old_budget", 0)
            if old_budget_nominal and new_budget_nominal > (old_budget_nominal * 1.5):
                raise ValueError(f"Presupuesto sugerido S/.{new_budget_nominal} excede la regla de seguridad del 150% (actual: S/.{old_budget_nominal}).")

            # Detectamos si es daily o lifetime budget por el backup
            if "lifetime_budget" in backup_state:
                post_data["lifetime_budget"] = int(new_budget_nominal * 100)
            else:
                post_data["daily_budget"] = int(new_budget_nominal * 100)

        elif action_type == "MODIFY_PLACEMENTS":
            # Cambiar placements (ej. quitar Instagram, solo Reels/Feed)
            new_placements = action.parameters.get("new_placements", [])
            if not new_placements:
                raise ValueError("Faltan los nuevos placements en parámetros.")
            
            # Regla de seguridad: SIEMPRE incluir facebook_reels
            if "facebook_reels" not in new_placements:
                new_placements.append("facebook_reels")
                
            # Regla de seguridad: NUNCA incluir instagram_positions si es IA Autónoma (según briefs)
            if "instagram" in new_placements:
                new_placements = [p for p in new_placements if "instagram" not in p]

            # Inyectar en estructura de targeting de Meta
            current_targeting = backup_state.get("targeting", {})
            current_targeting["publisher_platforms"] = new_placements
            post_data["targeting"] = current_targeting

        elif action_type == "MODIFY_TARGETING":
            # Modificar segmentación (edad, geo, etc.)
            new_targeting_updates = action.parameters.get("targeting_updates", {})
            current_targeting = backup_state.get("targeting", {})
            
            # Fusionar cambios
            merged_targeting = {**current_targeting, **new_targeting_updates}
            
            # Regla de seguridad: siempre mantener Lima Región (key:2771)
            geo = merged_targeting.get("geo_locations", {})
            regions = geo.get("regions", [])
            has_lima = any(r.get("key") == "2771" or r.get("key") == 2771 for r in regions)
            if not has_lima:
                regions.append({"key": "2771", "name": "Lima Region"})
                geo["regions"] = regions
                merged_targeting["geo_locations"] = geo

            post_data["targeting"] = merged_targeting

        elif action_type == "PAUSE_CAMPAIGN":
            post_data["status"] = "PAUSED"

        elif action_type == "RESUME_CAMPAIGN":
            post_data["status"] = "ACTIVE"

        else:
            raise ValueError(f"Acción de modificación no soportada: {action_type}")

        # Ejecutar la llamada POST
        logger.info(f"Modificando {target_id} - Acción: {action_type} - Params: {post_data}")
        result = await limiter.call_api(endpoint, post_data, method="POST", access_token=token)
        
        if "ERROR" in result or "error" in result:
            raise Exception(f"Meta API falló al aplicar la modificación: {result}")
            
        return result

    async def rollback(self, ad_account: AdAccount, action: AIAction) -> Dict[str, Any]:
        """
        Revierte una modificación realizada previamente utilizando el snapshot de respaldo.
        """
        backup_state = action.parameters.get("backup_state")
        if not backup_state:
            raise ValueError("No se encontró backup_state en los parámetros de la acción para realizar rollback.")

        limiter = MetaAPIRateLimiter(ad_account_id=ad_account.id)
        token = ad_account.get_access_token()

        target_id = _resolve_target_id(action)
        endpoint = f"{target_id}"

        # Construir post_data para restaurar el estado anterior
        post_data = {}
        
        if "daily_budget" in backup_state:
            post_data["daily_budget"] = backup_state["daily_budget"]
        elif "lifetime_budget" in backup_state:
            post_data["lifetime_budget"] = backup_state["lifetime_budget"]
            
        if "targeting" in backup_state:
            post_data["targeting"] = backup_state["targeting"]
            
        if "status" in backup_state:
            post_data["status"] = backup_state["status"]

        logger.info(f"REVIRTIDENDO MODIFICACIÓN en {target_id} - Restaurando: {post_data}")
        result = await limiter.call_api(endpoint, post_data, method="POST", access_token=token)
        
        if "ERROR" in result or "error" in result:
            raise Exception(f"Meta API falló al restaurar el estado previo: {result}")
            
        return result
