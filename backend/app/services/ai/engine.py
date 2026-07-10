import logging
import json
import datetime
from typing import Dict, Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.services.ai.rules import Capa1Rules
from app.services.ai.statistics import Capa2Statistics
from app.services.ai.llm import OpenRouterClient, PROMPT_VERSION
from app.services.ai.token_tracking import UsageContext
from app.services.ai.planner import AIPlanner

logger = logging.getLogger("lejovista.ai_engine")

class AIEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def run_analysis(self, org_id: str) -> Dict[str, Any]:
        """
        Ejecuta el pipeline completo de análisis en 3 capas para una organización.
        1. Capa 1: Reglas
        2. Capa 2: Estadística
        3. Capa 3: LLM (Narrativa + Generación de HTML Master Brief)
        4. AI Planner: Proponer acciones en base a los insights creados.
        """
        # Buscar la organización
        org = await self.db.get(Organization, org_id)
        if not org:
            raise ValueError(f"Organización {org_id} no encontrada.")

        # Buscar cuentas de anuncios conectadas
        q = select(AdAccount).where(AdAccount.organization_id == org_id)
        res = await self.db.execute(q)
        ad_accounts = res.scalars().all()

        total_insights = []
        total_actions = []
        
        # Guardaremos el último reporte HTML generado para retornar
        html_brief = ""

        for acc in ad_accounts:
            if acc.status != "ACTIVE":
                continue

            # 1. Capa 1: Reglas fijas
            rules_analyzer = Capa1Rules(self.db)
            insights_c1 = await rules_analyzer.analyze(acc)
            
            # 2. Capa 2: Estadísticas
            stats_analyzer = Capa2Statistics(self.db)
            insights_c2 = await stats_analyzer.analyze(acc)

            # Dedupe (B-1): cada análisis REEMPLAZA la foto anterior de la cuenta.
            # Sin esto, el análisis corre tras cada sync (+ trigger manual + Beat)
            # y la misma alerta se acumulaba repetida en /diagnostics hasta que
            # el usuario la descartara a mano una por una.
            await self.db.execute(
                update(AIInsight)
                .where(
                    AIInsight.ad_account_id == acc.id,
                    AIInsight.is_dismissed == False,  # noqa: E712
                )
                .values(is_dismissed=True)
            )

            # Guardar insights en DB
            all_insights = insights_c1 + insights_c2
            for ins in all_insights:
                self.db.add(ins)
            await self.db.flush()

            total_insights.extend(all_insights)

            # 3. Preparar resumen para el LLM (Capa 3)
            # Buscamos el estado resumido de campañas en los últimos 7 días
            raw_data = await self._get_recent_campaigns_summary(acc.id)
            
            insights_summary = "\n".join([
                f"- [{ins.level}] [{ins.category}] {ins.title}: {ins.description}" 
                for ins in all_insights
            ])

            # Cliente OpenRouter para la narrativa
            llm_client = OpenRouterClient()
            narrative, used_fallback = await llm_client.analyze_campaigns(
                org, acc, insights_summary, raw_data,
                usage_ctx=UsageContext(
                    feature="master_brief",
                    organization_id=org.id,
                    ad_account_id=acc.id,
                ),
            )

            # Generar HTML del Master Brief
            html_brief = await llm_client.generate_html_brief(org, acc, narrative, raw_data)

            # 4. Proponer acciones basadas en los insights
            planner = AIPlanner(self.db)
            proposed_actions = await planner.plan_actions(acc, all_insights)
            total_actions.extend(proposed_actions)

            # Guardamos la narrativa en el AdAccount para fácil acceso posterior,
            # junto con metadatos de trazabilidad (modelo, versión de prompt, timestamp).
            # C-4: si el LLM cayó a la plantilla local, se registra tal cual —
            # antes se estampaba el modelo/proveedor como si el LLM hubiera
            # respondido, y la UI mostraba un template como "Procesado por IA".
            acc.cached_narrative = narrative
            acc.narrative_generated_at = datetime.datetime.utcnow()
            acc.narrative_model = None if used_fallback else (org.ai_model or None)
            acc.narrative_provider = "fallback_template" if used_fallback else llm_client.provider
            acc.narrative_prompt_version = PROMPT_VERSION
            self.db.add(acc)

        await self.db.commit()
        
        return {
            "insights_count": len(total_insights),
            "actions_proposed": len(total_actions),
            "html_brief": html_brief
        }

    async def _get_recent_campaigns_summary(self, ad_account_id: str) -> dict:
        """
        Calcula estadísticas resumidas de los últimos 7 días por campaña para enviar al LLM.
        """
        import datetime
        today = datetime.date.today()
        seven_days_ago = today - datetime.timedelta(days=7)

        q = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account_id,
            CampaignSnapshot.date >= seven_days_ago
        )
        res = await self.db.execute(q)
        snapshots = res.scalars().all()

        campaigns = {}
        for s in snapshots:
            c_id = s.campaign_id
            if c_id not in campaigns:
                campaigns[c_id] = {
                    "name": s.campaign_name,
                    "spend": 0.0,
                    "conversions": 0,
                    "impressions": 0,
                    "clicks": 0,
                    "ctr": 0.0,
                    "budget": s.budget
                }
            campaigns[c_id]["spend"] += s.spend
            campaigns[c_id]["conversions"] += s.conversions
            campaigns[c_id]["impressions"] += s.impressions
            campaigns[c_id]["clicks"] += s.clicks
            
        # Recalcular CTR global de la semana
        for c_id, data in campaigns.items():
            if data["impressions"] > 0:
                data["ctr"] = (data["clicks"] / data["impressions"]) * 100.0

        return campaigns
