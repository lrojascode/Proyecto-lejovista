from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.schemas.schemas import (
    OrganizationResponse, OrganizationUpdate, AdAccountCreate, AdAccountResponse,
    MetaPageResponse, MetaPageWhatsAppUpdate,
)
from app.utils.security import get_current_user, require_role
from app.workers.tasks import sync_ad_account
from app.services.ai.llm import OpenRouterClient, MODELS_BY_PROVIDER
from app.services.policy_catalog import INDUSTRIES, get_policies_for_industry
from typing import List, Optional

router = APIRouter(prefix="/organizations", tags=["organizations"])

@router.get("/ai-models")
async def get_available_ai_models(current_user: User = Depends(get_current_user)):
    """
    Modelos de IA que el selector de Preferencias debería ofrecer: solo los del
    proveedor realmente activo (según qué API key esté configurada), para que
    la organización no pueda guardar un modelo que el proveedor activo no
    reconoce (ver AI-4/hallazgo del selector estático).
    """
    provider = OpenRouterClient().provider
    return {"active_provider": provider, "models": MODELS_BY_PROVIDER[provider]}

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
    current_user: User = Depends(require_role("ADMIN")),
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

@router.get("/my/policies")
async def get_my_business_policies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Recordatorios de políticas de Meta según el rubro configurado (ver
    app/services/policy_catalog.py) — contenido curado, no generado por IA.
    Incluye `available_industries` para que Configuración no necesite otro
    endpoint para poblar el selector de rubro."""
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    return {
        "industry": org.industry,
        "policies": get_policies_for_industry(org.industry),
        "available_industries": INDUSTRIES,
    }

@router.get("/my/ad-accounts", response_model=List[AdAccountResponse])
async def list_my_ad_accounts(
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Por defecto excluye las cuentas archivadas (soft-delete, ver
    archive_ad_account) — así el selector global y el resto de la app no las
    ofrecen para elegir. La pantalla de Configuración pide
    `include_archived=true` para poder mostrarlas y ofrecer "Restaurar".
    """
    conditions = [AdAccount.organization_id == current_user.organization_id]
    if not include_archived:
        conditions.append(AdAccount.status != "ARCHIVED")
    q = select(AdAccount).where(*conditions)
    res = await db.execute(q)
    return res.scalars().all()

@router.post("/my/ad-accounts/{ad_account_id}/archive", response_model=AdAccountResponse)
async def archive_ad_account(
    ad_account_id: str,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    """
    Soft-delete de una cuenta publicitaria (HANDOFF §10): en vez de borrar de
    verdad (irreversible, y `api_call_logs` no tiene `ondelete` configurado —
    fallaría con un error de integridad referencial), solo se marca
    `status=ARCHIVED`. Conserva todo el historial (snapshots, insights,
    acciones) y se puede revertir con `restore_ad_account`.
    """
    acc = await db.get(AdAccount, ad_account_id)
    if not acc or acc.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    acc.status = "ARCHIVED"
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc

@router.post("/my/ad-accounts/{ad_account_id}/restore", response_model=AdAccountResponse)
async def restore_ad_account(
    ad_account_id: str,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    """Revierte el archivado de archive_ad_account."""
    acc = await db.get(AdAccount, ad_account_id)
    if not acc or acc.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")
    if acc.status != "ARCHIVED":
        raise HTTPException(status_code=400, detail="La cuenta no está archivada.")

    acc.status = "ACTIVE"
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc

@router.get("/my/pages", response_model=List[MetaPageResponse])
async def list_my_pages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Páginas descubiertas por el sync (Fase 4 del plan de creación de campañas)
    con su WhatsApp conectado, para que el formulario de "Crear campaña" ofrezca
    un dropdown en vez de que el usuario tipee page_id/whatsapp_number a mano.
    """
    q = select(MetaPage).where(
        MetaPage.organization_id == current_user.organization_id,
        MetaPage.status == "ACTIVE",
    )
    res = await db.execute(q)
    return res.scalars().all()

@router.patch("/my/pages/{page_id}/whatsapp", response_model=MetaPageResponse)
async def set_page_whatsapp_number(
    page_id: str,
    payload: MetaPageWhatsAppUpdate,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db)
):
    """
    Fallback manual del WhatsApp de una página (Fase 4). El descubrimiento
    automático vía Graph API (`GET /{page_id}?fields=whatsapp_number`) depende
    de que el token tenga el permiso `whatsapp_business_management` —muchos
    tokens conectados no lo tienen (confirmado contra Meta real: el campo no
    da error, Meta simplemente lo omite en la respuesta). Sin este fallback,
    esas cuentas quedarían bloqueadas para siempre en "Crear campaña".
    """
    page = await db.get(MetaPage, page_id)
    if not page or page.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Página no encontrada para esta organización.")

    page.whatsapp_number = payload.whatsapp_number
    db.add(page)
    await db.commit()
    await db.refresh(page)
    return page

@router.post("/my/ad-accounts", response_model=AdAccountResponse)
async def connect_ad_account(
    acc_in: AdAccountCreate,
    current_user: User = Depends(require_role("ADMIN")),
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
