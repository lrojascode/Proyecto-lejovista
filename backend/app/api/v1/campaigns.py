from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.database import get_db
from app.models.user import User
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ad_account import AdAccount
from app.schemas.schemas import CampaignSnapshotResponse
from app.utils.security import get_current_user
from app.workers.tasks import sync_ad_account
from typing import List
import datetime

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

@router.get("/recent", response_model=List[CampaignSnapshotResponse])
async def list_recent_campaign_snapshots(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna la captura de métricas más reciente para cada campaña de la organización.
    """
    # 1. Buscar todas las campañas de los últimos 2 días para la organización
    two_days_ago = datetime.date.today() - datetime.timedelta(days=2)
    q = select(CampaignSnapshot).where(
        CampaignSnapshot.organization_id == current_user.organization_id,
        CampaignSnapshot.date >= two_days_ago
    ).order_by(CampaignSnapshot.campaign_id, desc(CampaignSnapshot.date))
    
    res = await db.execute(q)
    all_snapshots = res.scalars().all()
    
    # Filtrar para obtener solo el más reciente por campaign_id
    seen_campaigns = set()
    latest_snapshots = []
    
    for s in all_snapshots:
        if s.campaign_id not in seen_campaigns:
            seen_campaigns.add(s.campaign_id)
            latest_snapshots.append(s)
            
    return latest_snapshots

@router.get("/{campaign_id}/history", response_model=List[CampaignSnapshotResponse])
async def get_campaign_history(
    campaign_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna el historial de snapshots diarios de una campaña específica para dibujar gráficos.
    """
    q = select(CampaignSnapshot).where(
        CampaignSnapshot.organization_id == current_user.organization_id,
        CampaignSnapshot.campaign_id == campaign_id
    ).order_by(CampaignSnapshot.date.asc())
    
    res = await db.execute(q)
    return res.scalars().all()

@router.post("/sync-trigger")
async def trigger_manual_sync(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Dispara manualmente el worker de sincronización incremental para todas las cuentas del tenant.
    """
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status == "ACTIVE"
    )
    res = await db.execute(q)
    accounts = res.scalars().all()
    
    if not accounts:
        raise HTTPException(status_code=400, detail="No hay cuentas de anuncios activas conectadas.")

    for acc in accounts:
        sync_ad_account.delay(acc.id, "INCREMENTAL")
        
    return {"message": f"Sincronización manual programada para {len(accounts)} cuentas de anuncios."}
