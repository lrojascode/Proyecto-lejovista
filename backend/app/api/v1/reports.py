from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
import datetime
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.ai_insight import AIInsight
from app.models.sync_job import SyncJob
from app.services.ai.engine import AIEngine
from app.services.report_cache import get_report
from app.services.kpi_period import get_kpis_with_deltas
from app.utils.security import get_current_user, get_active_ad_account_id
from typing import Optional

router = APIRouter(prefix="/reports", tags=["reports"])

@router.get("/brief-data")
async def get_master_brief_data(
    window_days: int = 30,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna los datos estructurados del Master Brief en formato JSON, para la
    cuenta de anuncios activa (ver `get_active_ad_account_id`).

    5.2 (F-3.1): `window_days` (7/30/90) controla la ventana del reporte —
    cada ventana trae también su comparativa Δ% vs el período previo del
    mismo tamaño (`report.kpis.deltas_pct` y `campaign.*_delta_pct`).
    """
    if window_days not in (7, 30, 90):
        raise HTTPException(status_code=400, detail="window_days debe ser 7, 30 o 90.")
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
    report = await get_report(db, acc, window_days=window_days)

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


@router.get("/kpis-summary")
async def get_kpis_summary(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db),
):
    """5.1 (E-3): resumen liviano para la fila de KPIs del dashboard —
    gasto/conversaciones/costo por conversación de 7d con Δ% vs los 7d
    previos, conteo de alertas críticas activas y la fecha del último sync
    completado. No recorre snapshots por campaña (eso lo hace ReportBuilder,
    cacheado en 4.2) — aquí solo se necesitan los totales de la cuenta."""
    if not ad_account_id:
        raise HTTPException(status_code=400, detail="No se encontró una cuenta de anuncios activa.")

    kpis = await get_kpis_with_deltas(db, ad_account_id, window_days=7)

    critical_count = (await db.execute(
        select(func.count()).select_from(AIInsight).where(
            AIInsight.ad_account_id == ad_account_id,
            AIInsight.is_dismissed == False,  # noqa: E712
            AIInsight.level == "CRITICAL",
        )
    )).scalar_one()

    last_sync = (await db.execute(
        select(SyncJob.completed_at)
        .where(SyncJob.ad_account_id == ad_account_id, SyncJob.status == "COMPLETED")
        .order_by(desc(SyncJob.completed_at))
        .limit(1)
    )).scalar_one_or_none()

    return {
        "kpis_7d": kpis["current"],
        "kpis_prev_7d": kpis["previous"],
        "deltas_pct": kpis["deltas_pct"],
        "critical_alerts_count": critical_count,
        "last_sync_at": last_sync.isoformat() if last_sync else None,
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

