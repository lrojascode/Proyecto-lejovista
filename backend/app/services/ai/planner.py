import logging
import datetime
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.ad_account import AdAccount
from app.models.ai_insight import AIInsight
from app.models.ai_action import AIAction

logger = logging.getLogger("lejovista.planner")

class AIPlanner:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def plan_actions(self, ad_account: AdAccount, insights: List[AIInsight]) -> List[AIAction]:
        """
        Toma una lista de insights generados y crea propuestas de acción (AIActions)
        asociadas en la base de datos (estado PROPOSED).
        Evita duplicar acciones propuestas que ya estén pendientes.
        """
        actions = []
        
        for ins in insights:
            if not ins.suggested_action_type:
                continue

            # 1. Comprobar si ya existe una acción propuesta pendiente (PROPOSED o APPROVED)
            # para esta campaña e insight
            q = select(AIAction).where(
                AIAction.ad_account_id == ad_account.id,
                AIAction.campaign_id == ins.campaign_id if hasattr(AIAction, "campaign_id") else AIAction.target_campaign_id == ins.campaign_id,
                AIAction.action_type == ins.suggested_action_type,
                AIAction.status.in_(["PROPOSED", "APPROVED", "EXECUTING"])
            )
            res = await self.db.execute(q)
            existing = res.scalars().all()
            if existing:
                continue # Ya hay un cambio de este tipo en cola

            # 2. Generar los detalles y parámetros según el tipo de acción
            action = self._create_action_from_insight(ad_account, ins)
            if action:
                self.db.add(action)
                actions.append(action)

        if actions:
            await self.db.flush()
            logger.info(f"AI Planner propuso {len(actions)} nuevas acciones para la cuenta {ad_account.id}.")
            
        return actions

    def _create_action_from_insight(self, ad_account: AdAccount, insight: AIInsight) -> AIAction:
        """
        Construye una instancia de AIAction en base al insight y sus métricas.
        """
        action_type = insight.suggested_action_type
        c_id = insight.campaign_id

        # Valores por defecto
        risk = "LOW"
        title = ""
        description = ""
        parameters = {}
        planned_calls = []
        estimated_impact = ""
        confidence = 0.85

        # Analizar el tipo de acción
        if action_type == "PAUSE_CAMPAIGN":
            risk = "HIGH"  # Pausar campaña activa es escritura crítica
            title = f"Pausar campaña ineficiente"
            description = f"Pausar la campaña de inmediato debido a: {insight.description}"
            parameters = {
                "old_status": "ACTIVE",
                "new_status": "PAUSED"
            }
            planned_calls = [
                {"method": "POST", "endpoint": f"{c_id}", "body": {"status": "PAUSED"}}
            ]
            estimated_impact = "Evita desperdiciar presupuesto en campaña de alto costo por lead."
            confidence = 0.95

        elif action_type == "MODIFY_BUDGET":
            risk = "MEDIUM"
            # Determinamos si el insight es para escalar (+25%) o recortar (-30%)
            is_escalation = "Excelente" in insight.title or "a la baja" in insight.description
            
            if is_escalation:
                title = "Incrementar presupuesto en campaña ganadora"
                old_budget = 70.0 if c_id == "camp_cable_gpt" else 50.0
                new_budget = old_budget * 1.25 # +25%
                description = f"Incrementar presupuesto diario de S/.{old_budget:.2f} a S/.{new_budget:.2f} (+25%) debido a excelente rendimiento de CPL."
                parameters = {
                    "old_budget": old_budget,
                    "new_budget": new_budget
                }
                estimated_impact = f"Incrementa en ~20% la captación de leads manteniendo el CPL bajo."
                confidence = 0.80
            else:
                title = "Reducir presupuesto en campaña de CPL alto"
                old_budget = 50.0 if c_id == "camp_vulcanizado" else 40.0
                new_budget = old_budget * 0.70 # -30%
                description = f"Reducir presupuesto diario de S/.{old_budget:.2f} a S/.{new_budget:.2f} (-30%) debido al alza en la tendencia del CPL."
                parameters = {
                    "old_budget": old_budget,
                    "new_budget": new_budget
                }
                estimated_impact = "Reduce el gasto ineficiente en un 30% mientras se optimiza la segmentación."
                confidence = 0.85

            # El adset ID real se obtendría en producción, usamos el c_id como target
            planned_calls = [
                {"method": "POST", "endpoint": f"{c_id}", "body": {"daily_budget": int(new_budget * 100)}}
            ]

        elif action_type == "MODIFY_PLACEMENTS":
            risk = "LOW"
            title = "Optimizar Placements (Remover Instagram)"
            description = f"Remover la red de Instagram de los placements debido a CPL ineficiente, y priorizar Facebook Reels."
            parameters = {
                "old_placements": ["facebook_feed", "instagram_positions", "facebook_reels"],
                "new_placements": ["facebook_feed", "facebook_reels"]
            }
            planned_calls = [
                {"method": "POST", "endpoint": f"{c_id}", "body": {"targeting": {"publisher_platforms": ["facebook", "facebook_reels"]}}}
            ]
            estimated_impact = "El CPL promedio del adset podría bajar un 15-20% al remover placements caros."
            confidence = 0.90

        elif action_type == "CREATE_CAMPAIGN":
            risk = "HIGH"
            title = "Crear nueva campaña optimizada"
            # Nombre de campaña sin caracteres especiales (reglas de Meta)
            new_camp_name = f"MTV_MSJ_THHN_LIMA_PROMO_{datetime.date.today().strftime('%b%Y').upper()}"
            description = (
                f"Crear una campaña completamente nueva para promocionar 'THHN' en Lima, "
                f"reemplazando el anuncio ineficiente. Se creará en estado PAUSADO por seguridad."
            )
            parameters = {
                "campaign_name": new_camp_name,
                "objective": "OUTCOME_LEADS",
                "daily_budget": 70.0,
                "duration_days": 7,
                "targeting": {
                    "geo_locations": {"regions": [{"key": "2771", "name": "Lima Region"}]},
                    "age_min": 25,
                    "age_max": 65,
                    "publisher_platforms": ["facebook", "facebook_reels"]
                },
                "creative": {
                    "headline": "Cables Eléctricos de Alta Calidad - Envío Rápido",
                    "body": "Adquiere cables THHN con garantía y certificación. Solicita cotización inmediata por WhatsApp.",
                    "whatsapp_number": "51999999999",
                    "welcome_message": "Hola, estoy interesado en cotizar cables THHN."
                }
            }
            # En producción esto generaría las 6 llamadas HTTP planificadas descritas en el paso0:
            # 1. GET act_info, 2. POST image, 3. POST campaign, 4. POST adset, 5. POST creative, 6. POST ad
            planned_calls = [
                {"method": "GET", "endpoint": f"act_{ad_account.external_account_id}", "body": {}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/campaigns", "body": {"name": new_camp_name, "objective": "OUTCOME_LEADS", "status": "PAUSED"}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/adsets", "body": {"name": f"Adset - {new_camp_name}", "daily_budget": 7000, "status": "PAUSED"}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/adcreatives", "body": {"name": f"Creative - {new_camp_name}"}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/ads", "body": {"name": f"Ad - {new_camp_name}", "status": "PAUSED"}}
            ]
            estimated_impact = "Establece un nuevo canal de leads optimizado con CPL estimado de S/.2.50."
            confidence = 0.70

        else:
            return None

        return AIAction(
            insight_id=insight.id,
            ad_account_id=ad_account.id,
            organization_id=ad_account.organization_id,
            action_type=action_type,
            risk_level=risk,
            title=title,
            description=description,
            target_campaign_id=c_id,
            platform="META",
            parameters=parameters,
            planned_api_calls=planned_calls,
            status="PROPOSED",
            estimated_impact=estimated_impact,
            confidence=confidence
        )
