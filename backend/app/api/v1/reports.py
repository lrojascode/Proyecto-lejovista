from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import datetime
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.ai.engine import AIEngine
from app.utils.security import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])

@router.get("/brief-data")
async def get_master_brief_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna los datos estructurados del Master Brief en formato JSON.
    """
    # 1. Obtener organización
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    # 2. Obtener primera cuenta activa
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status == "ACTIVE"
    )
    res = await db.execute(q)
    acc = res.scalars().first()
    if not acc:
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

    return {
        "organization_name": org.name,
        "ad_account_name": acc.name,
        "external_account_id": acc.external_account_id,
        "generated_at": datetime.datetime.now().isoformat(),
        "narrative": narrative,
        "narrative_pending": narrative is None,
        "narrative_generated_at": acc.narrative_generated_at.isoformat() if acc.narrative_generated_at else None,
        "narrative_model": acc.narrative_model,
        "narrative_prompt_version": acc.narrative_prompt_version,
        "campaigns_data": raw_data
    }

