"""5.1/5.2 — Cálculo de KPIs por ventana de fechas + comparativa Δ%.

Extraído de `AIEngine._get_previous_period_kpis` (antes solo servía al
Master Brief v2) para reutilizarlo también en el resumen del dashboard
(5.1) y, más adelante, en la comparativa temporal del reporte (5.2).
"""
import datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_snapshot import CampaignSnapshot


async def days_of_history(db: AsyncSession, ad_account_id: str) -> int:
    """Cuántos días de datos hay realmente sincronizados para esta cuenta.

    Es el techo honesto de cualquier ventana: si alguien pregunta por los
    últimos 90 días y solo hay 10 sincronizados, responder "en los últimos 90
    días" daría por ceros 80 días que nunca se midieron. Devuelve 0 si no hay
    ningún snapshot.
    """
    oldest = (await db.execute(
        select(func.min(CampaignSnapshot.date)).where(
            CampaignSnapshot.ad_account_id == ad_account_id
        )
    )).scalar_one_or_none()
    if oldest is None:
        return 0
    return (datetime.date.today() - oldest).days + 1


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

    # `resultados = leads + conversaciones de WhatsApp`. Antes esto era
    # `s.conversations or s.conversions`: ELEGIA uno en vez de sumar, y en una
    # cuenta que corre formularios Y WhatsApp el total no era "resultados", era
    # "lo que haya".
    #
    # `results` lo llena el sync desde los `actions` crudos; es None en los
    # snapshots anteriores a la migración f6a7b8c9d0e1 y ahí se conserva el
    # comportamiento viejo, que es preferible a inventar una suma que
    # doble-contaría (`conversations` trae los leads como proxy).
    leads = sum(s.conversions for s in snapshots)
    results = sum(
        s.results if s.results is not None else (s.conversations or s.conversions)
        for s in snapshots
    )
    whatsapp = max(0, results - leads)
    cost_per_conv = round(spend / results, 2) if results > 0 else spend
    return {
        "spend": round(spend, 2),
        # `conversations` se conserva como el TOTAL para no romper a quien ya lo
        # lee (frontend, PDF, facts del chat). Las claves honestas son las de
        # abajo: el chat nombra cada cosa por lo que es.
        "conversations": results,
        "results": results,
        "leads": leads,
        "whatsapp": whatsapp,
        "cost_per_conv": cost_per_conv,
    }


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
