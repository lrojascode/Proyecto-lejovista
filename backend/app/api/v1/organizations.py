from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.models.whatsapp_number import WhatsAppNumber
from app.models.api_call_log import APICallLog
from app.models.ai_token_usage import AITokenUsage
from app.models.account_learning_snapshot import AccountLearningSnapshot
from app.models.campaign_learning_log import CampaignLearningLog
from app.schemas.schemas import (
    OrganizationResponse, OrganizationUpdate, AdAccountCreate, AdAccountResponse,
    AdAccountDefaultPageUpdate, MetaPageResponse, MetaPageWhatsAppUpdate,
    WhatsAppNumberResponse, WhatsAppNumberCreate, WhatsAppNumberUpdate,
)
from app.utils.security import get_current_user, require_role
from app.utils.throttle import enforce_cooldown
from app.services.meta_ads.token_exchange import exchange_for_long_lived_token
from app.workers.tasks import sync_ad_account, refresh_org_pages
from app.services.ai.llm import OpenRouterClient, MODELS_BY_PROVIDER
from app.services.policy_catalog import INDUSTRIES, get_policies_for_industry
from app.schemas.response_schemas import AiModelsResponse, BusinessPoliciesResponse, MessageResponse
from typing import List, Optional

router = APIRouter(prefix="/organizations", tags=["organizations"])

@router.get("/ai-models", response_model=AiModelsResponse)
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

@router.get("/my/policies", response_model=BusinessPoliciesResponse)
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

@router.delete("/my/ad-accounts/{ad_account_id}", response_model=MessageResponse)
async def delete_ad_account(
    ad_account_id: str,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    """
    Eliminación definitiva de una cuenta publicitaria archivada.
    Exige status == 'ARCHIVED' (no se borra una cuenta activa).
    """
    acc = await db.get(AdAccount, ad_account_id)
    if not acc or acc.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")
    if acc.status != "ARCHIVED":
        raise HTTPException(status_code=400, detail="Solo se pueden eliminar definitivamente cuentas previamente archivadas.")

    acc_name = acc.name

    # Desvincular tablas de auditoría/consumo/aprendizaje que conservan registros históricos (SET NULL)
    await db.execute(update(APICallLog).where(APICallLog.ad_account_id == acc.id).values(ad_account_id=None))
    await db.execute(update(AITokenUsage).where(AITokenUsage.ad_account_id == acc.id).values(ad_account_id=None))
    await db.execute(update(AccountLearningSnapshot).where(AccountLearningSnapshot.ad_account_id == acc.id).values(ad_account_id=None))
    await db.execute(update(CampaignLearningLog).where(CampaignLearningLog.ad_account_id == acc.id).values(ad_account_id=None))

    await db.delete(acc)
    await db.commit()
    return {"message": f"Cuenta '{acc_name}' eliminada definitivamente con éxito."}

@router.patch("/my/ad-accounts/{ad_account_id}/default-page", response_model=AdAccountResponse)
async def set_ad_account_default_page(
    ad_account_id: str,
    payload: AdAccountDefaultPageUpdate,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    """
    Fija (o limpia, con page_id=null) la página de Facebook predeterminada de
    una cuenta publicitaria. La usan el formulario de creación (preselección)
    y AIExecutor: con 2+ páginas en la org, las acciones CREATE_CAMPAIGN caían
    siempre en NEEDS_INPUT porque no había forma de designar cuál promocionar.
    """
    acc = await db.get(AdAccount, ad_account_id)
    if not acc or acc.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    if payload.page_id is None:
        acc.default_page_id = None
    else:
        page = await db.get(MetaPage, payload.page_id)
        if not page or page.organization_id != current_user.organization_id:
            raise HTTPException(status_code=404, detail="Página no encontrada para esta organización.")
        acc.default_page_id = page.id

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

@router.post("/my/pages/refresh", response_model=MessageResponse)
async def refresh_my_pages(
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db)
):
    """
    Re-descubre las páginas (y su WhatsApp) desde Meta para las cuentas ACTIVE
    de la organización, sin sync completo. Para cuando se acaba de otorgar
    acceso a una página nueva en el Business Manager y se quiere ver en el
    dropdown de inmediato (no a las 06:00 del día siguiente).
    """
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status == "ACTIVE",
    )
    res = await db.execute(q)
    accounts = res.scalars().all()
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail="No hay cuentas activas para refrescar páginas (¿token expirado? Reconecta la cuenta)."
        )

    # Mismo criterio que sync-trigger: golpea Meta, evitar spam. Se consume
    # solo si de verdad hay algo que refrescar.
    await enforce_cooldown("pages_refresh", current_user.organization_id, cooldown_seconds=60)

    refresh_org_pages.delay(current_user.organization_id)
    return {"message": f"Actualización de páginas programada para {len(accounts)} cuentas."}

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

    # Que el número quede también en el catálogo de la organización: si se cargó
    # para una página, sirve para cualquier campaña. Sin esto habría que
    # escribirlo dos veces.
    await _ensure_number_in_catalog(db, current_user.organization_id, payload.whatsapp_number)

    await db.commit()
    await db.refresh(page)
    return page


# --- Catálogo de números de WhatsApp (F1) ---
#
# `MetaPage.whatsapp_number` sigue siendo el número POR DEFECTO de cada página;
# esta lista es de la que se elige al crear una campaña, y admite varios.

async def _ensure_number_in_catalog(db: AsyncSession, org_id: str, number: str) -> None:
    """Agrega el número al catálogo si no estaba (idempotente)."""
    number = (number or "").strip()
    if not number:
        return
    exists = (await db.execute(select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == org_id,
        WhatsAppNumber.display_number == number,
    ))).scalar_one_or_none()
    if not exists:
        db.add(WhatsAppNumber(
            organization_id=org_id, display_number=number, source="MANUAL",
        ))


@router.get("/my/whatsapp-numbers", response_model=List[WhatsAppNumberResponse])
async def list_whatsapp_numbers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Números disponibles para elegir al crear una campaña."""
    q = select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == current_user.organization_id,
        WhatsAppNumber.status == "ACTIVE",
    ).order_by(WhatsAppNumber.source, WhatsAppNumber.display_number)
    return (await db.execute(q)).scalars().all()


@router.post("/my/whatsapp-numbers", response_model=WhatsAppNumberResponse,
             status_code=status.HTTP_201_CREATED)
async def add_whatsapp_number(
    payload: WhatsAppNumberCreate,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db),
):
    """Alta manual de un número.

    Es el camino principal, no un parche: el descubrimiento automático necesita
    el permiso `whatsapp_business_management`, que la mayoría de los tokens
    conectados no trae.
    """
    number = payload.display_number.strip()
    existing = (await db.execute(select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == current_user.organization_id,
        WhatsAppNumber.display_number == number,
    ))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Ese número ya está en la lista.")

    row = WhatsAppNumber(
        organization_id=current_user.organization_id,
        display_number=number, source="MANUAL",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/my/whatsapp-numbers/{number_id}", response_model=WhatsAppNumberResponse)
async def update_whatsapp_number(
    number_id: str,
    payload: WhatsAppNumberUpdate,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db),
):
    """Corrige un número ya guardado — que era justamente lo que no se podía
    hacer: la UI solo ofrecía el campo mientras estuviera vacío."""
    row = await _get_own_number(db, number_id, current_user.organization_id)
    if row.source == "META":
        raise HTTPException(
            status_code=400,
            detail="Este número viene de Meta y lo reescribe la sincronización. "
                   "Agrega uno manual si necesitas otro distinto.",
        )

    new_number = payload.display_number.strip()
    clash = (await db.execute(select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == current_user.organization_id,
        WhatsAppNumber.display_number == new_number,
        WhatsAppNumber.id != number_id,
    ))).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail="Ese número ya está en la lista.")

    row.display_number = new_number
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/my/whatsapp-numbers/{number_id}", response_model=MessageResponse)
async def delete_whatsapp_number(
    number_id: str,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_own_number(db, number_id, current_user.organization_id)
    if row.source == "META":
        raise HTTPException(
            status_code=400,
            detail="Este número viene de Meta: la sincronización volvería a agregarlo.",
        )
    await db.delete(row)
    await db.commit()
    return {"message": "Número eliminado."}


async def _get_own_number(db: AsyncSession, number_id: str, org_id: str) -> WhatsAppNumber:
    row = await db.get(WhatsAppNumber, number_id)
    if not row or row.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Número no encontrado para esta organización.")
    return row

@router.post("/my/ad-accounts", response_model=AdAccountResponse)
async def connect_ad_account(
    acc_in: AdAccountCreate,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    # 0. Los tokens pegados a mano suelen ser de corta duración (horas) — así se
    # cayó el pipeline el 12-jul (cuentas EXPIRED). Si hay app credentials reales
    # configuradas, se canjea por un long-lived (~60 días) antes de guardar; si el
    # intercambio no aplica o falla, se guarda el token original (best-effort).
    token_to_store, token_expires_at = await exchange_for_long_lived_token(acc_in.access_token)
    if not token_to_store:
        token_to_store, token_expires_at = acc_in.access_token, None

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
        existing.set_access_token(token_to_store, expires_at=token_expires_at)
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
        ad_acc.set_access_token(token_to_store, expires_at=token_expires_at)
        if acc_in.refresh_token:
            ad_acc.set_refresh_token(acc_in.refresh_token)
        db.add(ad_acc)

    await db.commit()

    # 3. Disparar Sincronización Inicial FULL en background
    sync_ad_account.delay(ad_acc.id, "FULL")
    
    return ad_acc
