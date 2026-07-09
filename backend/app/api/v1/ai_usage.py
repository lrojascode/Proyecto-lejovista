"""Endpoint de medición de consumo de IA (Ajuste 3).

Expone el gasto de tokens agregado por funcionalidad para la organización
activa, de modo que se pueda ver en qué se está usando la IA y cuánto cuesta.
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.ai_token_usage import AITokenUsage
from app.utils.security import get_current_user

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])


@router.get("")
async def get_ai_usage(
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Consumo de tokens de los últimos `days` días, agregado por funcionalidad.

    Scopeado a la organización del usuario. Devuelve totales globales y un
    desglose por `feature` (master_brief, campaign_copy, etc.) con tokens de
    prompt/respuesta/razonamiento, número de llamadas, cuántas usaron cadena de
    pensamiento y costo estimado en USD.
    """
    since = datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))
    org_id = current_user.organization_id

    base_where = [
        AITokenUsage.organization_id == org_id,
        AITokenUsage.created_at >= since,
    ]

    # Desglose por funcionalidad.
    q = (
        select(
            AITokenUsage.feature,
            func.count().label("calls"),
            func.coalesce(func.sum(AITokenUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AITokenUsage.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(AITokenUsage.reasoning_tokens), 0).label("reasoning_tokens"),
            func.coalesce(func.sum(AITokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AITokenUsage.estimated_cost_usd), 0.0).label("estimated_cost_usd"),
            func.coalesce(func.sum(func.cast(AITokenUsage.used_cot, Integer)), 0).label("cot_calls"),
        )
        .where(*base_where)
        .group_by(AITokenUsage.feature)
        .order_by(func.sum(AITokenUsage.total_tokens).desc())
    )
    rows = (await db.execute(q)).all()

    by_feature = [
        {
            "feature": r.feature,
            "calls": int(r.calls),
            "prompt_tokens": int(r.prompt_tokens),
            "completion_tokens": int(r.completion_tokens),
            "reasoning_tokens": int(r.reasoning_tokens),
            "total_tokens": int(r.total_tokens),
            "cot_calls": int(r.cot_calls),
            "estimated_cost_usd": round(float(r.estimated_cost_usd), 6),
        }
        for r in rows
    ]

    totals = {
        "calls": sum(f["calls"] for f in by_feature),
        "prompt_tokens": sum(f["prompt_tokens"] for f in by_feature),
        "completion_tokens": sum(f["completion_tokens"] for f in by_feature),
        "reasoning_tokens": sum(f["reasoning_tokens"] for f in by_feature),
        "total_tokens": sum(f["total_tokens"] for f in by_feature),
        "cot_calls": sum(f["cot_calls"] for f in by_feature),
        "estimated_cost_usd": round(sum(f["estimated_cost_usd"] for f in by_feature), 6),
    }

    return {"days": days, "totals": totals, "by_feature": by_feature}
