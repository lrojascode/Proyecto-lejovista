"""5.1/5.2 — Cálculo de KPIs por ventana de fechas + comparativa Δ%.

Extraído de `AIEngine._get_previous_period_kpis` (antes solo servía al
Master Brief v2) para reutilizarlo también en el resumen del dashboard
(5.1) y, más adelante, en la comparativa temporal del reporte (5.2).
"""
import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_snapshot import CampaignSnapshot


async def get_window_kpis(
    db: AsyncSession, ad_account_id: str, start: datetime.date, end: datetime.date
) -> Dict[str, float]:
    """KPIs agregados en `[start, end)` — el límite superior es exclusivo."""
    q = select(CampaignSnapshot).where(
        CampaignSnapshot.ad_account_id == ad_account_id,
        CampaignSnapshot.date >= start,
        CampaignSnapshot.date < end,
    )
    snapshots = (await db.execute(q)).scalars().all()

    spend = sum(s.spend for s in snapshots)
    conversations = sum((s.conversations or s.conversions) for s in snapshots)
    cost_per_conv = round(spend / conversations, 2) if conversations > 0 else spend
    return {"spend": round(spend, 2), "conversations": conversations, "cost_per_conv": cost_per_conv}


def pct_change(old: float, new: float) -> Optional[float]:
    if not old:
        return None
    return round((new - old) / old * 100, 1)


async def get_kpis_with_deltas(
    db: AsyncSession, ad_account_id: str, window_days: int = 7
) -> Dict[str, Any]:
    """KPIs de la ventana actual vs la ventana anterior del mismo tamaño,
    con el Δ% de cada métrica (spend/conversations/cost_per_conv)."""
    today = datetime.date.today()
    current_start = today - datetime.timedelta(days=window_days)
    prev_start = today - datetime.timedelta(days=window_days * 2)

    current = await get_window_kpis(db, ad_account_id, current_start, today)
    previous = await get_window_kpis(db, ad_account_id, prev_start, current_start)

    deltas_pct = {
        "spend": pct_change(previous["spend"], current["spend"]),
        "conversations": pct_change(previous["conversations"], current["conversations"]),
        "cost_per_conv": pct_change(previous["cost_per_conv"], current["cost_per_conv"]),
    }
    return {"current": current, "previous": previous, "deltas_pct": deltas_pct}
