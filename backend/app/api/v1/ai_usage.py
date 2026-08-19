"""Endpoint de medición de consumo de IA (Fase 6.8 / Roadmap 11).

Expone el gasto de tokens agregado por funcionalidad, modelo, cuenta publicitaria
y línea de tiempo diaria para la organización activa.
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, Integer, Date, cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.ai_token_usage import AITokenUsage
from app.models.ad_account import AdAccount
from app.utils.security import get_current_user
from app.schemas.response_schemas import AiUsageResponse

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])


@router.get("", response_model=AiUsageResponse)
async def get_ai_usage(
    days: int = Query(30, ge=1, le=365),
    ad_account_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Consumo de tokens de los últimos `days` días con desgloses por:
    - feature (operación)
    - model / provider
    - ad_account
    - daily_timeline (tendencia diaria)
    """
    since = datetime.datetime.utcnow() - datetime.timedelta(days=max(1, days))
    org_id = current_user.organization_id

    base_where = [
        AITokenUsage.organization_id == org_id,
        AITokenUsage.created_at >= since,
    ]
    if ad_account_id:
        base_where.append(AITokenUsage.ad_account_id == ad_account_id)

    # 1. Desglose por funcionalidad (feature)
    q_feature = (
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
    rows_feature = (await db.execute(q_feature)).all()

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
        for r in rows_feature
    ]

    # 2. Desglose por modelo y proveedor
    q_model = (
        select(
            AITokenUsage.provider,
            AITokenUsage.model,
            func.count().label("calls"),
            func.coalesce(func.sum(AITokenUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AITokenUsage.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(AITokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AITokenUsage.estimated_cost_usd), 0.0).label("estimated_cost_usd"),
        )
        .where(*base_where)
        .group_by(AITokenUsage.provider, AITokenUsage.model)
        .order_by(func.sum(AITokenUsage.total_tokens).desc())
    )
    rows_model = (await db.execute(q_model)).all()

    by_model = [
        {
            "provider": r.provider,
            "model": r.model,
            "calls": int(r.calls),
            "prompt_tokens": int(r.prompt_tokens),
            "completion_tokens": int(r.completion_tokens),
            "total_tokens": int(r.total_tokens),
            "estimated_cost_usd": round(float(r.estimated_cost_usd), 6),
        }
        for r in rows_model
    ]

    # 3. Desglose por cuenta publicitaria
    q_account = (
        select(
            AITokenUsage.ad_account_id,
            AdAccount.name.label("ad_account_name"),
            func.count().label("calls"),
            func.coalesce(func.sum(AITokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AITokenUsage.estimated_cost_usd), 0.0).label("estimated_cost_usd"),
        )
        .outerjoin(AdAccount, AITokenUsage.ad_account_id == AdAccount.id)
        .where(*base_where)
        .group_by(AITokenUsage.ad_account_id, AdAccount.name)
        .order_by(func.sum(AITokenUsage.total_tokens).desc())
    )
    rows_account = (await db.execute(q_account)).all()

    by_ad_account = [
        {
            "ad_account_id": r.ad_account_id,
            "ad_account_name": r.ad_account_name or "General (Sin cuenta vinculada)",
            "calls": int(r.calls),
            "total_tokens": int(r.total_tokens),
            "estimated_cost_usd": round(float(r.estimated_cost_usd), 6),
        }
        for r in rows_account
    ]

    # 4. Línea de tiempo diaria
    date_col = cast(AITokenUsage.created_at, Date).label("usage_date")
    q_daily = (
        select(
            date_col,
            func.count().label("calls"),
            func.coalesce(func.sum(AITokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AITokenUsage.estimated_cost_usd), 0.0).label("estimated_cost_usd"),
        )
        .where(*base_where)
        .group_by(date_col)
        .order_by(date_col.asc())
    )
    rows_daily = (await db.execute(q_daily)).all()

    daily_timeline = [
        {
            "date": str(r.usage_date),
            "calls": int(r.calls),
            "total_tokens": int(r.total_tokens),
            "estimated_cost_usd": round(float(r.estimated_cost_usd), 6),
        }
        for r in rows_daily
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

    return {
        "days": days,
        "totals": totals,
        "by_feature": by_feature,
        "by_model": by_model,
        "by_ad_account": by_ad_account,
        "daily_timeline": daily_timeline,
    }
