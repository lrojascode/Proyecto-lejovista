import logging
import datetime
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.ad_account import AdAccount
from app.models.ai_insight import AIInsight
from app.models.ai_action import AIAction
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.organization import Organization
from app.services.brand_code import derive_brand_code
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_targeting import build_targeting, _facebook_positions
from app.services.ai.llm import OpenRouterClient
from app.services.ai.token_tracking import UsageContext
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES

logger = logging.getLogger("lejovista.planner")

# Tipos de acción cuya escritura real en Meta ocurre a nivel de adset, no de
# campaña (AI-2). PAUSE_CAMPAIGN/RESUME_CAMPAIGN sí son a nivel de campaña.
ADSET_LEVEL_ACTIONS = {"MODIFY_BUDGET", "MODIFY_PLACEMENTS", "MODIFY_TARGETING"}


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

        # Mapa campaign_id -> presupuesto del snapshot más reciente. Se usa como
        # base real para MODIFY_BUDGET en lugar de valores hardcodeados (AI-2).
        # También capturamos el nombre real de la campaña (name_map), usado por
        # CREATE_CAMPAIGN para dar contexto de producto al generador de copy
        # (Fase 3B) en vez del literal "THHN" hardcodeado para cualquier cuenta.
        # B-2: acotar a los últimos 30 días — solo el snapshot más reciente por
        # campaña/adset importa aquí, no hace falta cargar el historial completo
        # de la cuenta (que crece sin límite con el tiempo).
        history_cutoff = datetime.date.today() - datetime.timedelta(days=30)

        budget_map = {}
        name_map: Dict[str, str] = {}
        q_snaps = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account.id,
            CampaignSnapshot.date >= history_cutoff,
        ).order_by(CampaignSnapshot.date.desc())
        res_snaps = await self.db.execute(q_snaps)
        for s in res_snaps.scalars().all():
            if s.campaign_id not in budget_map:
                budget_map[s.campaign_id] = s.budget
                name_map[s.campaign_id] = s.campaign_name

        # Mapa campaign_id -> adset_ids vigentes (según el snapshot más reciente de
        # cada campaña). Se usa para resolver el target real de las escrituras a
        # nivel de adset en vez de escribir sobre el nodo de campaña (AI-2).
        adset_map: Dict[str, List[str]] = {}
        latest_date_by_campaign: Dict[str, datetime.date] = {}
        # A-4: resumen de targeting REAL (del snapshot más reciente de cada
        # adset) para poder documentar old_placements con datos reales en vez
        # de una lista hardcodeada en MODIFY_PLACEMENTS.
        adset_targeting_map: Dict[str, dict] = {}
        q_adsets = select(AdsetSnapshot).where(
            AdsetSnapshot.ad_account_id == ad_account.id,
            AdsetSnapshot.date >= history_cutoff,
        ).order_by(AdsetSnapshot.date.desc())
        res_adsets = await self.db.execute(q_adsets)
        for a in res_adsets.scalars().all():
            latest = latest_date_by_campaign.setdefault(a.campaign_id, a.date)
            if a.date == latest:
                adset_map.setdefault(a.campaign_id, []).append(a.adset_id)
            adset_targeting_map.setdefault(a.adset_id, a.targeting_summary or {})

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
            action = await self._create_action_from_insight(
                ad_account, ins, budget_map.get(ins.campaign_id), adset_map.get(ins.campaign_id, []),
                name_map.get(ins.campaign_id), adset_targeting_map,
            )
            if action:
                self.db.add(action)
                actions.append(action)

        if actions:
            await self.db.flush()
            logger.info(f"AI Planner propuso {len(actions)} nuevas acciones para la cuenta {ad_account.id}.")
            
        return actions

    async def _create_action_from_insight(
        self,
        ad_account: AdAccount,
        insight: AIInsight,
        current_budget: Optional[float] = None,
        adset_ids: Optional[List[str]] = None,
        existing_campaign_name: Optional[str] = None,
        adset_targeting_map: Optional[Dict[str, dict]] = None,
    ) -> Optional[AIAction]:
        """
        Construye una instancia de AIAction en base al insight y sus métricas.
        """
        action_type = insight.suggested_action_type
        c_id = insight.campaign_id
        adset_ids = adset_ids or []

        # Valores por defecto
        risk = "LOW"
        title = ""
        description = ""
        parameters = {}
        planned_calls = []
        estimated_impact = ""
        confidence = 0.85
        target_adset_id = None

        # AI-2: las acciones a nivel de adset necesitan un adset_id real y sin
        # ambigüedad para poder escribir con confianza. Si la campaña no tiene
        # exactamente un adset vigente, no proponemos la acción a ciegas.
        if action_type in ADSET_LEVEL_ACTIONS:
            if len(adset_ids) != 1:
                logger.warning(
                    "%s omitido para campaña %s: se encontraron %d adsets vigentes "
                    "(se requiere exactamente 1 para resolver el target sin ambigüedad).",
                    action_type, c_id, len(adset_ids),
                )
                return None
            target_adset_id = adset_ids[0]

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

            # AI-2: usar el presupuesto REAL del snapshot más reciente. Sin un
            # presupuesto conocido no proponemos un cambio a ciegas.
            if not current_budget or current_budget <= 0:
                logger.warning(
                    "MODIFY_BUDGET omitido para campaña %s: presupuesto actual desconocido.", c_id
                )
                return None
            old_budget = float(current_budget)

            # Determinamos si el insight es para escalar (+25%) o recortar (-30%)
            is_escalation = "Excelente" in insight.title or "a la baja" in insight.description

            if is_escalation:
                title = "Incrementar presupuesto en campaña ganadora"
                new_budget = round(old_budget * 1.25, 2) # +25%
                description = f"Incrementar presupuesto diario de S/.{old_budget:.2f} a S/.{new_budget:.2f} (+25%) debido a excelente rendimiento de CPL."
                estimated_impact = "Incrementa la captación de leads manteniendo el CPL bajo."
                confidence = 0.80
            else:
                title = "Reducir presupuesto en campaña de CPL alto"
                new_budget = round(old_budget * 0.70, 2) # -30%
                description = f"Reducir presupuesto diario de S/.{old_budget:.2f} a S/.{new_budget:.2f} (-30%) debido al alza en la tendencia del CPL."
                estimated_impact = "Reduce el gasto ineficiente en un 30% mientras se optimiza la segmentación."
                confidence = 0.85

            parameters = {
                "old_budget": old_budget,
                "new_budget": new_budget
            }

            # AI-2: el presupuesto en Meta vive a nivel de adset, no de campaña —
            # target_adset_id ya se resolvió arriba contra adset_snapshots reales.
            planned_calls = [
                {"method": "POST", "endpoint": f"{target_adset_id}", "body": {"daily_budget": int(new_budget * 100)}}
            ]

        elif action_type == "MODIFY_PLACEMENTS":
            risk = "LOW"

            # A-4: el placement que el insight señaló para excluir. rules.py
            # genera el título con el patrón "... : {pl_name}" únicamente para
            # el caso "Instagram ineficiente" (el caso "Oportunidad en Reels"
            # es para priorizar, no para excluir nada, y required_placements
            # ya mantiene facebook_reels siempre presente).
            flagged_placement = insight.title.rsplit(":", 1)[-1].strip() if ":" in insight.title else None
            if not flagged_placement:
                logger.warning(
                    "MODIFY_PLACEMENTS omitido para campaña %s: el insight no señala un "
                    "placement concreto a excluir.", c_id,
                )
                return None

            # old_placements REAL: publisher_platforms del snapshot más reciente
            # del adset (AI-2/A-4), no una lista hardcodeada para cualquier cuenta.
            adset_targeting = (adset_targeting_map or {}).get(target_adset_id) or {}
            old_placements = adset_targeting.get("publisher_platforms") or ["facebook"]

            # Meta espera publisher_platforms genérico ("facebook") + las
            # posiciones concretas en facebook_positions — antes se enviaba
            # "facebook_reels" como si fuera un publisher_platform, valor
            # inválido para ese campo (bug A-4).
            new_positions = _facebook_positions([flagged_placement])

            title = f"Optimizar Placements (remover {flagged_placement.replace('_', ' ')})"
            description = (
                f"Remover el placement '{flagged_placement}' de los placements activos del adset "
                f"debido a CPL ineficiente. {insight.description}"
            )
            parameters = {
                "old_placements": old_placements,
                "new_placements": new_positions,
                "excluded_placement": flagged_placement,
            }
            planned_calls = [
                {"method": "POST", "endpoint": f"{target_adset_id}", "body": {
                    "targeting": {"publisher_platforms": ["facebook"], "facebook_positions": new_positions}
                }}
            ]
            estimated_impact = "El CPL promedio del adset podría bajar al remover un placement ineficiente."
            confidence = 0.90

        elif action_type == "CREATE_CAMPAIGN":
            risk = "HIGH"
            title = "Crear nueva campaña optimizada"

            # Fase 3: parámetros estructurales del motor de sugerencia determinista
            # (3A, sin LLM, deriva de datos reales) + copy generado por el LLM (3B).
            # Reemplaza el stub que hardcodeaba "THHN"/Lima/S/.2.50 para cualquier cuenta.
            suggestion = await CampaignSuggester(self.db).suggest(ad_account)
            org = await self.db.get(Organization, ad_account.organization_id)
            product_name = existing_campaign_name or "Producto"
            copy = await OpenRouterClient().suggest_campaign_copy(
                org, ad_account, product_name=product_name,
                winning_context=suggestion.get("winning_campaign"),
                winning_ad_copy=suggestion.get("winning_ad_copy"),
                usage_ctx=UsageContext(
                    feature="campaign_copy_action",
                    organization_id=ad_account.organization_id,
                    ad_account_id=ad_account.id,
                ),
            )

            brand = derive_brand_code(ad_account)
            month_year = datetime.date.today().strftime("%b%Y").upper()
            new_camp_name = f"{brand}_MSJ_{product_name}_{month_year}".replace(" ", "")

            description = (
                f"Crear una campaña nueva para reemplazar '{product_name}' (CTR bajo), con "
                f"parámetros derivados del histórico real de la cuenta y copy generado por IA. "
                f"Se creará en estado PAUSADO por seguridad."
            )
            parameters = {
                "campaign_name": new_camp_name,
                # A-1: nombre real del producto, para que el executor pueda
                # despachar la misma Saga de creation_tasks (que recalcula el
                # nombre de campaña a partir de product_name + marca + mes)
                # sin tener que volver a consultar el suggester.
                "product_name": product_name,
                "objective": suggestion["objective"],
                "daily_budget": suggestion["suggested_budget"],
                "duration_days": suggestion["suggested_days"],
                "excluded_placements": suggestion["excluded_placements"],
                # Señales crudas (además del targeting ya construido abajo, que
                # queda como preview/documentación): la Saga real recalcula el
                # targeting desde estas señales, no desde el dict ya armado.
                "best_age_range": suggestion.get("best_age_range"),
                "best_device": suggestion.get("best_device"),
                # Targeting optimizado desde el histórico ganador (Ajuste 2):
                # edad/dispositivo/placements reales, no hardcodeados.
                "targeting": build_targeting(
                    best_age_range=suggestion.get("best_age_range"),
                    best_device=suggestion.get("best_device"),
                    excluded_placements=suggestion.get("excluded_placements") or [],
                ),
                "creative": {
                    # 3.4: el copy v2 trae 2 variantes de headline/primary_text
                    # para que un humano elija en la UI; en el flujo 100% IA
                    # (esta acción se aprueba sin editor de copy) se toma la
                    # primera variante.
                    "headline": copy["headline"][0],
                    "body": copy["primary_text"][0],
                    "description": copy["description"],
                    "welcome_message": copy["welcome_message"],
                },
                "suggestion_evidence": suggestion["evidence"],
            }
            # En producción esto generaría las 6 llamadas HTTP planificadas descritas en el paso0:
            # 1. GET act_info, 2. POST image, 3. POST campaign, 4. POST adset, 5. POST creative, 6. POST ad
            planned_calls = [
                {"method": "GET", "endpoint": f"act_{ad_account.external_account_id}", "body": {}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/campaigns", "body": {"name": new_camp_name, "objective": suggestion["objective"], "status": "PAUSED"}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/adsets", "body": {"name": f"Adset - {new_camp_name}", "daily_budget": int(suggestion["suggested_budget"] * 100), "status": "PAUSED"}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/adcreatives", "body": {"name": f"Creative - {new_camp_name}"}},
                {"method": "POST", "endpoint": f"act_{ad_account.external_account_id}/ads", "body": {"name": f"Ad - {new_camp_name}", "status": "PAUSED"}}
            ]
            # Sin cifras inventadas (AI-1): la justificación es la evidencia real
            # del suggester, y la confianza baja si no hay histórico de la cuenta.
            estimated_impact = suggestion["evidence"][0] if suggestion["evidence"] else "Sin histórico suficiente para estimar impacto."
            confidence = 0.75 if suggestion["has_history"] else 0.5

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
            target_adset_id=target_adset_id,
            platform="META",
            parameters=parameters,
            planned_api_calls=planned_calls,
            status="PROPOSED",
            estimated_impact=estimated_impact,
            confidence=confidence
        )
