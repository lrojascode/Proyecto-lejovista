from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import datetime
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.services.ai.engine import AIEngine
from app.services.report_cache import get_report
from app.utils.security import get_current_user, get_active_ad_account_id
from typing import Optional

router = APIRouter(prefix="/reports", tags=["reports"])

@router.get("/brief-data")
async def get_master_brief_data(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna los datos estructurados del Master Brief en formato JSON, para la
    cuenta de anuncios activa (ver `get_active_ad_account_id`).
    """
    # 1. Obtener organización
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    # 2. Obtener la cuenta activa (antes: silenciosamente "la primera ACTIVE" de
    # la org, que mezclaba datos si había 2+ cuentas — ver plan de aislamiento).
    if not ad_account_id:
        raise HTTPException(status_code=400, detail="No se encontró una cuenta de anuncios activa para generar el informe.")
    acc = await db.get(AdAccount, ad_account_id)
    if not acc or acc.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="No se encontró una cuenta de anuncios activa para generar el informe.")

    # 3. Compilar datos de los últimos 7 días
    engine = AIEngine(db)
    raw_data = await engine._get_recent_campaigns_summary(acc.id)
    if not raw_data:
        raise HTTPException(status_code=400, detail="No hay suficientes datos registrados para compilar un Master Brief. Ejecuta una sincronización primero.")

    # 4. A-4: este endpoint es de SOLO LECTURA. La narrativa se genera en background
    #    (AIEngine vía Celery, disparado por /insights/analyze-trigger). Si aún no
    #    existe, se devuelve None + narrative_pending para que el frontend la dispare.
    narrative = acc.cached_narrative

    # P-2.4/2.5: reporte estructurado completo (KPIs, adsets, ads, segmentación,
    # audiencias, creativos, alertas, parámetros óptimos) calculado desde la DB.
    report = await get_report(db, acc)

    return {
        "organization_name": org.name,
        "ad_account_name": acc.name,
        "external_account_id": acc.external_account_id,
        "generated_at": datetime.datetime.now().isoformat(),
        "narrative": narrative,
        "narrative_pending": narrative is None,
        "narrative_generated_at": acc.narrative_generated_at.isoformat() if acc.narrative_generated_at else None,
        "narrative_model": acc.narrative_model,
        "narrative_provider": acc.narrative_provider,
        "narrative_prompt_version": acc.narrative_prompt_version,
        "report": report,
        "campaigns_data": raw_data
    }


@router.get("/spend-history")
async def get_spend_history(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db),
):
    """Histórico de inversión mensual (sin desglose por campaña) de la cuenta
    de anuncios activa — poblado por `MetaAdsSyncService._sync_spend_history`."""
    if not ad_account_id:
        raise HTTPException(status_code=400, detail="No se encontró una cuenta de anuncios activa.")

    rows = (await db.execute(
        select(AdAccountSpendMonthly)
        .where(AdAccountSpendMonthly.ad_account_id == ad_account_id)
        .order_by(AdAccountSpendMonthly.year_month.asc())
    )).scalars().all()

    return {
        "ad_account_id": ad_account_id,
        "months": [
            {"year_month": r.year_month, "spend": round(r.spend, 2), "closed": r.closed}
            for r in rows
        ],
    }

