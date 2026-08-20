"""Aprendizaje longitudinal por sincronización (Q2).

Cada vez que corre el análisis (encadenado tras cada sync), se persiste un
`AccountLearningSnapshot` con el rendimiento REAL medido de la cuenta y cómo
cambió respecto a la sincronización anterior. Todo DETERMINISTA (regla AI-1, sin
LLM ni cifras inventadas): las cifras salen del ReportBuilder (misma fuente que
/reporte) y las observaciones son comparaciones aritméticas contra el snapshot
previo.

Esto complementa el `CampaignLearningLog` (que aprende de las campañas que la IA
crea) con la otra mitad: la trayectoria real de toda la cuenta en el tiempo.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_learning_snapshot import AccountLearningSnapshot
from app.models.ad_account import AdAccount
from app.services.kpi_period import pct_change
from app.utils.formatting import money

logger = logging.getLogger("lejovista.account_learning")

# Umbral (%) de variación del costo/conversación para llamar a la tendencia
# mejora/empeoramiento en vez de "estable". Movimientos menores son ruido.
_TREND_THRESHOLD_PCT = 10.0


def _kpis_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    spend = round(float(report["kpis"]["total_spend"]), 2)
    conversations = int(report["kpis"]["total_conversations"])
    cost_per_conv = round(spend / conversations, 2) if conversations > 0 else spend
    return {"spend": spend, "conversations": conversations, "cost_per_conv": cost_per_conv}


def _best_segment(bucket: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Entrada con menor costo/conversación de un grupo de segmentación.

    `bucket` es {nombre: {spend, conversions}} (ver ReportBuilder._segmentation);
    el costo se calcula aquí y solo cuentan entradas con conversiones > 0."""
    best = None
    for name, e in (bucket or {}).items():
        conv = int(e.get("conversions", 0) or 0)
        spend = float(e.get("spend", 0) or 0)
        if conv <= 0:
            continue
        cost = round(spend / conv, 2)
        if best is None or cost < best["cost_per_conv"]:
            best = {"name": name, "cost_per_conv": cost, "conversations": conv}
    return best


def _segmentation_winners(report: Dict[str, Any]) -> Dict[str, Any]:
    seg = report.get("segmentation", {}) or {}
    out: Dict[str, Any] = {}
    for key, group in (("placement", "placements"), ("device", "device"), ("age", "age")):
        best = _best_segment(seg.get(group, {}))
        if best:
            out[key] = best
    return out


def _top_bottom(report: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    campaigns = report.get("campaigns", [])  # ya ordenadas por costo/conv asc
    winners = [c for c in campaigns if c.get("conversations", 0) > 0]
    slim = lambda c: {
        "name": c["name"],
        "cost_per_conv": c["cost_per_conv"],
        "conversations": c["conversations"],
    }
    top = [slim(c) for c in winners[:3]]
    bottom = [slim(c) for c in campaigns[-3:]] if campaigns else []
    return top, bottom


def _build_observations(
    kpis: Dict[str, Any],
    deltas: Dict[str, Any],
    seg: Dict[str, Any],
    prev: Optional[AccountLearningSnapshot],
) -> List[str]:
    """Aprendizajes en lenguaje llano, comparando SIEMPRE contra números reales."""
    obs: List[str] = []

    if kpis["conversations"] <= 0:
        obs.append("La cuenta no registró conversaciones en esta ventana — sin señal de rendimiento que evaluar.")
        return obs

    if prev is None:
        obs.append(
            f"Primera medición de referencia: {kpis['conversations']} conversaciones a "
            f"{money(kpis['cost_per_conv'])} cada una, con {money(kpis['spend'])} de gasto."
        )
        return obs

    prev_kpis = prev.kpis_json or {}
    d_cost = deltas.get("cost_per_conv")
    if d_cost is not None and prev_kpis.get("cost_per_conv"):
        if abs(d_cost) < 1:
            obs.append(
                f"El costo por conversación se mantuvo prácticamente igual vs la sincronización anterior "
                f"({money(prev_kpis['cost_per_conv'])} → {money(kpis['cost_per_conv'])})."
            )
        else:
            direction = "bajó" if d_cost < 0 else "subió"
            obs.append(
                f"El costo por conversación {direction} {abs(d_cost):.0f}% vs la sincronización anterior "
                f"({money(prev_kpis['cost_per_conv'])} → {money(kpis['cost_per_conv'])})."
            )

    d_conv = deltas.get("conversations")
    if d_conv is not None and prev_kpis.get("conversations"):
        if abs(d_conv) < 1:
            obs.append(
                f"El volumen de conversaciones se mantuvo estable "
                f"({prev_kpis['conversations']} → {kpis['conversations']})."
            )
        else:
            direction = "más" if d_conv >= 0 else "menos"
            obs.append(
                f"Se generaron {abs(d_conv):.0f}% {direction} conversaciones que en la sincronización previa "
                f"({prev_kpis['conversations']} → {kpis['conversations']})."
            )

    # Segmento eficiente que se sostiene entre sincronizaciones (señal de patrón,
    # no de ruido puntual).
    prev_seg = prev.segmentation_json or {}
    for key, label in (("device", "dispositivo"), ("placement", "placement"), ("age", "rango de edad")):
        now_best = (seg.get(key) or {}).get("name")
        prev_best = (prev_seg.get(key) or {}).get("name")
        if now_best and prev_best and now_best == prev_best:
            obs.append(
                f"El {label} '{now_best}' se mantiene como el más eficiente por segunda sincronización seguida."
            )

    return obs


def _verdict(kpis: Dict[str, Any], deltas: Dict[str, Any], prev: Optional[AccountLearningSnapshot]) -> str:
    if kpis["conversations"] <= 0:
        return "NO_DATA"
    if prev is None:
        return "BASELINE"
    d_cost = deltas.get("cost_per_conv")
    if d_cost is None:
        return "STABLE"
    # Baja el costo/conversación = mejora.
    if d_cost <= -_TREND_THRESHOLD_PCT:
        return "IMPROVING"
    if d_cost >= _TREND_THRESHOLD_PCT:
        return "DEGRADING"
    return "STABLE"


async def get_latest_snapshot(db: AsyncSession, ad_account_id: str) -> Optional[AccountLearningSnapshot]:
    return (await db.execute(
        select(AccountLearningSnapshot)
        .where(AccountLearningSnapshot.ad_account_id == ad_account_id)
        .order_by(desc(AccountLearningSnapshot.created_at))
        .limit(1)
    )).scalar_one_or_none()


async def recent_trajectory(db: AsyncSession, ad_account_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Últimos N snapshots (más reciente primero), en forma compacta y
    serializable — para alimentar el contexto del LLM y el endpoint de UI."""
    rows = (await db.execute(
        select(AccountLearningSnapshot)
        .where(AccountLearningSnapshot.ad_account_id == ad_account_id)
        .order_by(desc(AccountLearningSnapshot.created_at))
        .limit(max(1, min(limit, 50)))
    )).scalars().all()
    return [
        {
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "verdict": r.verdict,
            "kpis": r.kpis_json,
            "deltas": r.deltas_json,
            "observations": r.observations_json,
        }
        for r in rows
    ]


async def record_sync_learning(
    db: AsyncSession,
    ad_account: AdAccount,
    report: Dict[str, Any],
    previous: Optional[AccountLearningSnapshot],
    window_days: int = 7,
) -> AccountLearningSnapshot:
    """Computa y persiste (flush, no commit) el snapshot de aprendizaje de esta
    sincronización. El commit lo hace el llamador (AIEngine.run_analysis)."""
    kpis = _kpis_from_report(report)

    prev_kpis = (previous.kpis_json if previous else None) or {}
    deltas = {
        "spend": pct_change(prev_kpis.get("spend", 0.0), kpis["spend"]) if previous else None,
        "conversations": pct_change(prev_kpis.get("conversations", 0.0), kpis["conversations"]) if previous else None,
        "cost_per_conv": pct_change(prev_kpis.get("cost_per_conv", 0.0), kpis["cost_per_conv"]) if previous else None,
    }

    top, bottom = _top_bottom(report)
    seg = _segmentation_winners(report)
    observations = _build_observations(kpis, deltas, seg, previous)
    verdict = _verdict(kpis, deltas, previous)

    snapshot = AccountLearningSnapshot(
        organization_id=ad_account.organization_id,
        ad_account_id=ad_account.id,
        window_days=window_days,
        kpis_json=kpis,
        deltas_json=deltas,
        top_json=top,
        bottom_json=bottom,
        segmentation_json=seg,
        observations_json=observations,
        verdict=verdict,
    )
    db.add(snapshot)
    await db.flush()
    logger.info(
        "Learning snapshot registrado para cuenta %s: %s (%d conversaciones, S/.%.2f/conv)",
        ad_account.id, verdict, kpis["conversations"], kpis["cost_per_conv"],
    )
    return snapshot
