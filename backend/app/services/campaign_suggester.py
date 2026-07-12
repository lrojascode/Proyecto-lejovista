import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.campaign_learning_log import CampaignLearningLog
from app.services.report_cache import get_report
from app.services.campaign_learning import assess_maturity, build_analysis
from app.tenant_rules.campaign_defaults import get_campaign_rules_for_org

logger = logging.getLogger("lejovista.suggester")


class CampaignSuggester:
    """Fase 3A del plan de creación de campañas: sugiere los parámetros
    ESTRUCTURALES de una campaña nueva (objetivo, presupuesto, placements a
    excluir, edad/dispositivo con mejor costo) a partir del rendimiento real
    de la cuenta.

    Reutiliza `ReportBuilder` (los mismos números que ya se muestran en el
    Master Brief) — nunca inventa cifras (regla AI-1). Si la cuenta no tiene
    histórico con conversiones, cae a defaults conservadores marcados
    explícitamente como "sin evidencia" en vez de fingir que salen de datos
    reales.

    Deliberadamente NO decide copy/texto — eso es responsabilidad del LLM en
    `OpenRouterClient.suggest_campaign_copy` (Fase 3B), para no mezclar
    cifras (deterministas) con redacción creativa.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def suggest(self, ad_account: AdAccount, budget_hint: Optional[float] = None) -> Dict[str, Any]:
        report = await get_report(self.db, ad_account)
        # M-2: reglas de campaña resueltas para la org (con overrides si los tiene).
        org = await self.db.get(Organization, ad_account.organization_id)
        rules = get_campaign_rules_for_org(org)

        campaigns = report["campaigns"]
        ads = report["ads"]
        segmentation = report["segmentation"]
        # `campaigns` ya viene ordenado por costo/conversación ascendente (ReportBuilder._agg_campaigns).
        winners = [c for c in campaigns if c["conversations"] > 0]
        best = winners[0] if winners else None

        # `ads` también viene ordenado por costo/conversación ascendente
        # (ReportBuilder._agg_ads). El copy real (título/cuerpo) del anuncio
        # ganador — antes el sync solo capturaba la imagen, nunca el texto —
        # permite que el LLM se inspire en lo que YA funcionó, no solo en el
        # estilo genérico del playbook (Fase 3B).
        winning_ad = next(
            (a for a in ads if a["conversations"] > 0 and a.get("creative_body")), None
        )

        # Playbook §4: "detecta desperdicio" — placements con gasto pero 0 conversiones.
        placements = segmentation.get("placements", {})
        excluded_placements = sorted(
            k for k, v in placements.items()
            if v.get("spend", 0) > 0 and v.get("conversions", 0) == 0
        )

        best_device = self._best_key(segmentation.get("device", {}))
        best_age_range = self._best_key(segmentation.get("age", {}))

        costs = [c["cost_per_conv"] for c in campaigns if c["conversations"] > 0]
        # Colchón de ~20 conversiones al costo más bajo observado — punto de partida
        # razonable, no una cifra mágica: se documenta en la evidencia de dónde sale.
        suggested_budget = budget_hint or (round(min(costs) * 20, 2) if costs else 100.0)

        evidence: List[str] = []
        if best:
            evidence.append(
                f"Campaña más eficiente de la cuenta: '{best['name']}' — "
                f"S/.{best['cost_per_conv']:.2f} por conversación, CTR {best['ctr']:.2f}% "
                f"({best['conversations']} conversaciones en los últimos {report['window_days']} días)."
            )
        else:
            evidence.append(
                "Sin histórico de campañas con conversiones todavía en esta cuenta — "
                "los valores por defecto NO se derivan de datos reales, ajústalos con cuidado."
            )

        if excluded_placements:
            evidence.append(
                "Placements con gasto y 0 conversiones (se sugiere excluir): "
                + ", ".join(p.replace("_", " ") for p in excluded_placements)
            )
        if best_device:
            evidence.append(f"Dispositivo con menor costo por conversión: {best_device}.")
        if best_age_range:
            evidence.append(f"Rango de edad con menor costo por conversión: {best_age_range}.")
        if winning_ad:
            evidence.append(
                f"Copy real capturado del anuncio '{winning_ad['name']}' "
                f"(S/.{winning_ad['cost_per_conv']:.2f} por conversación) — disponible como inspiración."
            )

        # Ajuste 2A: madurez/convergencia + razonamiento explicable de la IA,
        # derivados del mismo histórico real (deterministas, sin LLM).
        # Fase 1 (1.2): la madurez pesa además los resultados REALES de las
        # campañas que la propia IA creó (outcome loop).
        evaluated_outcomes = await self._own_evaluated_outcomes(ad_account)
        maturity = assess_maturity(campaigns, evaluated_outcomes)
        analysis = build_analysis(
            report,
            best=best,
            excluded_placements=excluded_placements,
            best_device=best_device,
            best_age_range=best_age_range,
            suggested_budget=suggested_budget,
            maturity=maturity,
            evaluated_outcomes=evaluated_outcomes,
        )

        return {
            "has_history": best is not None,
            "maturity": maturity,
            "analysis": analysis,
            "objective": rules["objective"],
            "optimization_goal": rules["optimization_goal"],
            "destination_type": rules["destination_type"],
            "suggested_budget": suggested_budget,
            "suggested_days": 7,
            "excluded_placements": excluded_placements,
            "best_device": best_device,
            "best_age_range": best_age_range,
            "winning_campaign": {
                "name": best["name"],
                "cost_per_conv": best["cost_per_conv"],
                "ctr": best["ctr"],
                "conversations": best["conversations"],
            } if best else None,
            "winning_ad_copy": {
                "title": winning_ad["creative_title"],
                "body": winning_ad["creative_body"],
                "cta_type": winning_ad["creative_cta_type"],
            } if winning_ad else None,
            "evidence": evidence,
        }

    async def _own_evaluated_outcomes(self, ad_account: AdAccount, limit: int = 10) -> List[Dict[str, Any]]:
        """Outcomes reales (outcome_json) de las campañas que la IA creó en esta
        cuenta y el outcome loop ya evaluó, del más reciente al más viejo."""
        rows = (await self.db.execute(
            select(CampaignLearningLog.outcome_json)
            .where(
                CampaignLearningLog.ad_account_id == ad_account.id,
                CampaignLearningLog.status == "EVALUATED",
                CampaignLearningLog.outcome_json.is_not(None),
            )
            .order_by(desc(CampaignLearningLog.created_at))
            .limit(limit)
        )).scalars().all()
        return list(rows)

    @staticmethod
    def _best_key(bucket: Dict[str, Any]) -> Optional[str]:
        items = [(k, v) for k, v in bucket.items() if v.get("conversions", 0) > 0]
        if not items:
            return None
        return min(items, key=lambda kv: (kv[1]["spend"] / kv[1]["conversions"]))[0]
