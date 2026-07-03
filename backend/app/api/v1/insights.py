from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.ai_insight import AIInsight
from app.models.ad_account import AdAccount
from app.schemas.schemas import AIInsightResponse
from app.utils.security import get_current_user
from app.utils.throttle import enforce_cooldown
from app.workers.tasks import run_ai_analysis
from typing import List

router = APIRouter(prefix="/insights", tags=["insights"])

@router.get("", response_model=List[AIInsightResponse])
async def list_active_insights(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna la lista de insights generados por la IA que no han sido descartados.
    """
    q = select(AIInsight).where(
        AIInsight.organization_id == current_user.organization_id,
        AIInsight.is_dismissed == False
    ).order_by(AIInsight.created_at.desc())
    
    res = await db.execute(q)
    return res.scalars().all()

@router.post("/{insight_id}/dismiss")
async def dismiss_insight(
    insight_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Descarta un insight de la vista del panel.
    """
    insight = await db.get(AIInsight, insight_id)
    if not insight or insight.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Insight no encontrado.")
        
    insight.is_dismissed = True
    db.add(insight)
    await db.commit()
    
    return {"message": "Insight descartado con éxito."}

@router.post("/analyze-trigger")
async def trigger_manual_analysis(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Dispara manualmente el análisis en 3 capas de la IA en background.
    """
    # Cooldown: el análisis invoca al LLM (costo). Evitar spam.
    await enforce_cooldown("analyze", current_user.organization_id, cooldown_seconds=120)

    # Programar la tarea de celery
    run_ai_analysis.delay(current_user.organization_id)
    return {"message": "Análisis e insights de IA programados para ejecutarse en segundo plano."}
