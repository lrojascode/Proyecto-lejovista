from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.schemas.schemas import OrganizationResponse, OrganizationUpdate, AdAccountCreate, AdAccountResponse
from app.utils.security import get_current_user
from app.workers.tasks import sync_ad_account
from typing import List

router = APIRouter(prefix="/organizations", tags=["organizations"])

@router.get("/my", response_model=OrganizationResponse)
async def get_my_organization(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    return org

@router.put("/my", response_model=OrganizationResponse)
async def update_my_organization(
    org_update: OrganizationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
        
    for k, v in org_update.model_dump(exclude_unset=True).items():
        setattr(org, k, v)
        
    db.add(org)
    await db.commit()
    return org

@router.get("/my/ad-accounts", response_model=List[AdAccountResponse])
async def list_my_ad_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    q = select(AdAccount).where(AdAccount.organization_id == current_user.organization_id)
    res = await db.execute(q)
    return res.scalars().all()

@router.post("/my/ad-accounts", response_model=AdAccountResponse)
async def connect_ad_account(
    acc_in: AdAccountCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 1. Comprobar si ya está conectado el mismo ad account para la misma organización
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.platform == acc_in.platform,
        AdAccount.external_account_id == acc_in.external_account_id
    )
    res = await db.execute(q)
    existing = res.scalar_one_or_none()
    
    if existing:
        # Si la cuenta ya existe, simplemente actualizamos el token
        existing.set_access_token(acc_in.access_token)
        if acc_in.refresh_token:
            existing.set_refresh_token(acc_in.refresh_token)
        existing.status = "ACTIVE"
        db.add(existing)
        ad_acc = existing
    else:
        # 2. Crear nueva cuenta y cifrar credenciales
        ad_acc = AdAccount(
            organization_id=current_user.organization_id,
            platform=acc_in.platform,
            external_account_id=acc_in.external_account_id,
            name=acc_in.name,
            status="ACTIVE"
        )
        ad_acc.set_access_token(acc_in.access_token)
        if acc_in.refresh_token:
            ad_acc.set_refresh_token(acc_in.refresh_token)
        db.add(ad_acc)

    await db.commit()

    # 3. Disparar Sincronización Inicial FULL en background
    sync_ad_account.delay(ad_acc.id, "FULL")
    
    return ad_acc
