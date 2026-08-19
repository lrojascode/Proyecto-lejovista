import datetime
import hashlib
import os
import secrets
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.invitation import Invitation
from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.models.whatsapp_number import WhatsAppNumber
from app.models.api_call_log import APICallLog
from app.models.ai_token_usage import AITokenUsage
from app.models.account_learning_snapshot import AccountLearningSnapshot
from app.models.campaign_learning_log import CampaignLearningLog
from dataclasses import asdict
from app.schemas.schemas import (
    OrganizationResponse, OrganizationUpdate, LLMKeyTestRequest, LLMKeyTestResponse,
    AdAccountCreate, AdAccountReconnect, AdAccountResponse,
    AdAccountDefaultPageUpdate, MetaPageResponse, MetaPageWhatsAppUpdate,
    WhatsAppNumberResponse, WhatsAppNumberCreate, WhatsAppNumberUpdate,
    CampaignRulesConfigUpdate, AIThresholdsConfigUpdate,
)
from app.schemas.invitation_schemas import InvitationCreateRequest
from app.schemas.response_schemas import (
    AiModelsResponse, BusinessPoliciesResponse, MessageResponse, InvitationResponse,
    CampaignRulesConfigResponse, AIThresholdsConfigResponse,
)
from app.utils.security import get_current_user, require_role
from app.utils.throttle import enforce_cooldown
from app.services.meta_ads.token_exchange import exchange_for_long_lived_token
from app.workers.tasks import sync_ad_account, refresh_org_pages
from app.services.ai.llm import OpenRouterClient, MODELS_BY_PROVIDER
from app.services.policy_catalog import INDUSTRIES, get_policies_for_industry
from app.services.email_service import send_email
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES, get_campaign_rules_for_org
from app.services.ai.thresholds import DEFAULT_THRESHOLDS, get_thresholds_for_org, _THRESHOLD_FIELDS
from typing import List, Optional, Dict, Any

router = APIRouter(prefix="/organizations", tags=["organizations"])

@router.get("/ai-models", response_model=AiModelsResponse)
async def get_available_ai_models(
    provider: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Modelos de IA que el selector de Preferencias debería ofrecer: si se pasa un
    proveedor específico, sus modelos; si no, los del proveedor activo de la
    organización o del sistema.
    """
    if provider and provider in MODELS_BY_PROVIDER:
        return {"active_provider": provider, "models": MODELS_BY_PROVIDER[provider]}

    org = await db.get(Organization, current_user.organization_id)
    client = OpenRouterClient(organization=org)
    active_provider = client.provider
    return {"active_provider": active_provider, "models": MODELS_BY_PROVIDER.get(active_provider, [])}

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
@router.patch("/my", response_model=OrganizationResponse)
async def update_my_organization(
    org_update: OrganizationUpdate,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
        
    dump = org_update.model_dump(exclude_unset=True)
    if "llm_api_key" in dump:
        new_key = dump.pop("llm_api_key")
        org.set_llm_api_key(new_key)

    for k, v in dump.items():
        setattr(org, k, v)
        
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org


_ALLOWED_LOGO_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
_MAX_LOGO_BYTES = 5 * 1024 * 1024  # 5MB


@router.post("/my/logo")
async def upload_org_logo(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    """Sube el logo de la organización (PNG, JPG, WebP, SVG)."""
    ext = _ALLOWED_LOGO_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="Formato de logo no soportado. Usa PNG, JPG, WebP o SVG.",
        )

    contents = await file.read()
    if len(contents) > _MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=400, detail="El logo supera el límite de 5MB."
        )

    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    logos_dir = os.path.join(settings.MEDIA_ROOT, "logos")
    os.makedirs(logos_dir, exist_ok=True)

    content_hash = hashlib.sha256(contents).hexdigest()[:12]
    filename = f"{org.id}_{content_hash}{ext}"
    filepath = os.path.join(logos_dir, filename)

    with open(filepath, "wb") as f:
        f.write(contents)

    logo_url = f"/media/logos/{filename}"
    org.logo_url = logo_url
    db.add(org)
    await db.commit()
    await db.refresh(org)

    return {"logo_url": logo_url}

@router.post("/my/llm-key/test", response_model=LLMKeyTestResponse)
async def test_llm_key(
    payload: LLMKeyTestRequest,
    current_user: User = Depends(require_role("ADMIN")),
):
    """
    Prueba una API Key de LLM contra el proveedor seleccionado antes de guardarla.
    """
    client = OpenRouterClient(api_key=payload.api_key, provider=payload.provider)
    valid, message = await client.test_key(model=payload.model)
    return {"valid": valid, "message": message, "provider": payload.provider}

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
    # 4.2: Normalizar external_account_id
    clean_ext_id = acc_in.external_account_id.strip()
    if acc_in.platform.upper() == "META" and not clean_ext_id.startswith("act_") and clean_ext_id.isdigit():
        clean_ext_id = f"act_{clean_ext_id}"

    # 0. Los tokens pegados a mano suelen ser de corta duración (horas) — así se
    # cayó el pipeline el 12-jul (cuentas EXPIRED). Si hay app credentials reales
    # configuradas, se canjea por un long-lived (~60 días) antes de guardar; si el
    # intercambio no aplica o falla, se guarda el token original (best-effort).
    token_to_store, token_expires_at = await exchange_for_long_lived_token(acc_in.access_token)
    if not token_to_store:
        token_to_store, token_expires_at = acc_in.access_token, None

    # 4.3: Validar el token contra Graph API antes de guardar si no estamos en mock
    from app.config import settings
    if (
        not settings.MOCK_SERVICES
        and acc_in.platform.upper() == "META"
        and not token_to_store.startswith("mock")
        and not token_to_store.startswith("test")
    ):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                test_res = await http_client.get(
                    f"https://graph.facebook.com/v19.0/{clean_ext_id}",
                    params={"fields": "id,name,account_status", "access_token": token_to_store},
                )
                data = test_res.json()
                if test_res.status_code != 200 or "error" in data:
                    err_msg = data.get("error", {}).get("message", "Token inválido o sin acceso a la cuenta")
                    err_code = data.get("error", {}).get("code")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Meta Ads rechazó el token (Código {err_code}): {err_msg}. Verifica permisos (ads_read, ads_management) y acceso a {clean_ext_id}."
                    )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo verificar el token contra Meta Graph API: {e}"
            )

    # 1. Comprobar si ya está conectado el mismo ad account para la misma organización
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.platform == acc_in.platform,
        AdAccount.external_account_id == clean_ext_id
    )
    res = await db.execute(q)
    existing = res.scalar_one_or_none()

    if existing:
        # Si la cuenta ya existe, simplemente actualizamos el token
        existing.set_access_token(token_to_store, expires_at=token_expires_at)
        if acc_in.refresh_token:
            existing.set_refresh_token(acc_in.refresh_token)
        existing.status = "ACTIVE"
        existing.last_sync_error_code = None
        existing.last_sync_error_detail = None
        db.add(existing)
        ad_acc = existing
    else:
        # 2. Crear nueva cuenta y cifrar credenciales
        ad_acc = AdAccount(
            organization_id=current_user.organization_id,
            platform=acc_in.platform,
            external_account_id=clean_ext_id,
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


@router.post("/my/ad-accounts/{id}/reconnect", response_model=AdAccountResponse)
async def reconnect_ad_account(
    id: str,
    payload: AdAccountReconnect,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db)
):
    """
    4.4: Reconexión simplificada de una cuenta EXPIRED con un nuevo token.
    """
    acc = await db.get(AdAccount, id)
    if not acc or acc.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Cuenta de anuncios no encontrada.")

    token_to_store, token_expires_at = await exchange_for_long_lived_token(payload.access_token)
    if not token_to_store:
        token_to_store, token_expires_at = payload.access_token, None

    from app.config import settings
    if (
        not settings.MOCK_SERVICES
        and acc.platform.upper() == "META"
        and not token_to_store.startswith("mock")
        and not token_to_store.startswith("test")
    ):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                test_res = await http_client.get(
                    f"https://graph.facebook.com/v19.0/{acc.external_account_id}",
                    params={"fields": "id,name,account_status", "access_token": token_to_store},
                )
                data = test_res.json()
                if test_res.status_code != 200 or "error" in data:
                    err_msg = data.get("error", {}).get("message", "Token inválido o sin acceso a la cuenta")
                    err_code = data.get("error", {}).get("code")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Meta Ads rechazó el token (Código {err_code}): {err_msg}."
                    )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo verificar el token contra Meta Graph API: {e}"
            )

    acc.set_access_token(token_to_store, expires_at=token_expires_at)
    acc.status = "ACTIVE"
    acc.last_sync_error_code = None
    acc.last_sync_error_detail = None
    db.add(acc)
    await db.commit()

    sync_ad_account.delay(acc.id, "FULL")
    return acc


# --- INVITACIONES Y EQUIPO (Roadmap 4) ---

@router.get("/my/members")
async def list_org_members(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista todos los usuarios miembros de la organización activa con sus roles."""
    q = (
        select(User.id, User.email, User.full_name, OrganizationUser.role, OrganizationUser.created_at)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(OrganizationUser.organization_id == current_user.organization_id)
        .order_by(OrganizationUser.created_at.asc())
    )
    res = await db.execute(q)
    members = []
    for row in res.all():
        members.append({
            "id": row.id,
            "email": row.email,
            "full_name": row.full_name,
            "role": row.role,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return members


@router.get("/my/invitations", response_model=List[InvitationResponse])
async def list_org_invitations(
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    """Lista las invitaciones enviadas para la organización activa."""
    org = await db.get(Organization, current_user.organization_id)
    q = (
        select(Invitation)
        .where(Invitation.organization_id == current_user.organization_id)
        .order_by(Invitation.created_at.desc())
    )
    res = await db.execute(q)
    invitations = res.scalars().all()
    out = []
    for inv in invitations:
        invite_url = f"http://{org.slug}.{settings.BASE_DOMAIN}/invitations/{inv.token}"
        out.append(
            InvitationResponse(
                id=inv.id,
                email=inv.email,
                role=inv.role,
                organization_id=inv.organization_id,
                token=inv.token,
                invite_url=invite_url,
                expires_at=inv.expires_at.isoformat(),
                accepted_at=inv.accepted_at.isoformat() if inv.accepted_at else None,
                created_at=inv.created_at.isoformat(),
            )
        )
    return out


@router.post("/my/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
async def create_org_invitation(
    payload: InvitationCreateRequest,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    """Crea una invitación para unirse a la organización y le envía un correo."""
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    # 1. Comprobar si ya es miembro
    q_existing_user = (
        select(User.id)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(User.email == payload.email, OrganizationUser.organization_id == org.id)
    )
    if (await db.execute(q_existing_user)).scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Este usuario ya es miembro de tu organización."
        )

    # 2. Generar token y fecha de expiración (7 días)
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=7)

    inv = Invitation(
        organization_id=org.id,
        email=payload.email,
        role=payload.role,
        token=token,
        expires_at=expires_at,
        invited_by_user_id=current_user.id,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)

    invite_url = f"http://{org.slug}.{settings.BASE_DOMAIN}/invitations/{token}"

    # 3. Enviar email de invitación
    subject = f"Has sido invitado a unirte a {org.name} en LejoVista"
    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #333;">
        <h2>¡Hola!</h2>
        <p>Has sido invitado a formar parte del equipo de <strong>{org.name}</strong> en LejoVista con el rol de <strong>{payload.role}</strong>.</p>
        <p>Haz clic en el siguiente enlace para aceptar la invitación y configurar tu cuenta:</p>
        <p style="margin: 24px 0;">
            <a href="{invite_url}" style="background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                Aceptar Invitación
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">Este enlace es válido por 7 días.</p>
    </div>
    """
    await send_email(
        to_email=payload.email,
        subject=subject,
        html_content=html_content,
        text_content=f"Has sido invitado a {org.name} en LejoVista. Acepta tu invitación aquí: {invite_url}",
    )

    return InvitationResponse(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        organization_id=inv.organization_id,
        token=inv.token,
        invite_url=invite_url,
        expires_at=inv.expires_at.isoformat(),
        accepted_at=None,
        created_at=inv.created_at.isoformat(),
    )


@router.delete("/my/invitations/{invitation_id}", response_model=MessageResponse)
async def delete_org_invitation(
    invitation_id: str,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    """Revoca o elimina una invitación pendiente."""
    inv = await db.get(Invitation, invitation_id)
    if not inv or inv.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Invitación no encontrada.")

    await db.delete(inv)
    await db.commit()
    return {"message": "Invitación revocada correctamente."}


# --- REGLAS DE CAMPAÑA Y UMBRALES IA (Roadmap 6 / Fase 6.3) ---

_VALID_OPTIMIZATION_GOALS = {
    "CONVERSATIONS", "LEAD_GENERATION", "LINK_CLICKS", "OFFSITE_CONVERSATIONS",
    "IMPRESSIONS", "REACH", "LANDING_PAGE_VIEWS", "POST_ENGAGEMENT",
}
_VALID_OBJECTIVES = {
    "OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT",
    "OUTCOME_AWARENESS", "OUTCOME_APP_PROMOTION",
}
_VALID_DESTINATIONS = {
    "WHATSAPP", "MESSENGER", "WEBSITE", "ON_AD", "INSTAGRAM_DIRECT",
}


@router.get("/my/campaign-rules", response_model=CampaignRulesConfigResponse)
async def get_my_campaign_rules(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve las reglas de campaña efectivas para la organización, sus overrides y los defaults globales."""
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    return CampaignRulesConfigResponse(
        rules=get_campaign_rules_for_org(org),
        overrides=org.campaign_rules_overrides or {},
        defaults=dict(TENANT_CAMPAIGN_RULES),
    )


@router.put("/my/campaign-rules", response_model=CampaignRulesConfigResponse)
async def update_my_campaign_rules(
    payload: CampaignRulesConfigUpdate,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza los overrides de reglas de campaña con validación estricta (evita fallos tardíos en la Saga)."""
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    overrides = payload.overrides or {}

    # Validaciones
    if "optimization_goal" in overrides:
        goal = overrides["optimization_goal"]
        if goal not in _VALID_OPTIMIZATION_GOALS:
            raise HTTPException(
                status_code=400,
                detail=f"optimization_goal '{goal}' inválido. Opciones válidas: {sorted(_VALID_OPTIMIZATION_GOALS)}",
            )

    if "objective" in overrides:
        obj = overrides["objective"]
        if obj not in _VALID_OBJECTIVES:
            raise HTTPException(
                status_code=400,
                detail=f"objective '{obj}' inválido. Opciones válidas: {sorted(_VALID_OBJECTIVES)}",
            )

    if "destination_type" in overrides:
        dest = overrides["destination_type"]
        if dest not in _VALID_DESTINATIONS:
            raise HTTPException(
                status_code=400,
                detail=f"destination_type '{dest}' inválido. Opciones válidas: {sorted(_VALID_DESTINATIONS)}",
            )

    if "max_campaigns_per_window" in overrides:
        val = overrides["max_campaigns_per_window"]
        if not isinstance(val, int) or val <= 0:
            raise HTTPException(
                status_code=400, detail="max_campaigns_per_window debe ser un entero positivo."
            )

    if "campaign_window_hours" in overrides:
        val = overrides["campaign_window_hours"]
        if not isinstance(val, (int, float)) or val <= 0:
            raise HTTPException(
                status_code=400, detail="campaign_window_hours debe ser un número positivo."
            )

    # Bloquear mutación de claves no overrideables
    for forbidden_key in ("default_status", "forbidden_status"):
        if forbidden_key in overrides:
            overrides.pop(forbidden_key)

    org.campaign_rules_overrides = overrides
    db.add(org)
    await db.commit()
    await db.refresh(org)

    return CampaignRulesConfigResponse(
        rules=get_campaign_rules_for_org(org),
        overrides=org.campaign_rules_overrides or {},
        defaults=dict(TENANT_CAMPAIGN_RULES),
    )


@router.get("/my/ai-thresholds", response_model=AIThresholdsConfigResponse)
async def get_my_ai_thresholds(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve los umbrales de IA efectivos para la organización, sus overrides y los defaults del sistema."""
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    return AIThresholdsConfigResponse(
        thresholds=asdict(get_thresholds_for_org(org)),
        overrides=org.ai_thresholds_overrides or {},
        defaults=asdict(DEFAULT_THRESHOLDS),
    )


@router.put("/my/ai-thresholds", response_model=AIThresholdsConfigResponse)
async def update_my_ai_thresholds(
    payload: AIThresholdsConfigUpdate,
    current_user: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza los umbrales de IA de la organización con validación de claves numéricas positivas."""
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    overrides = payload.overrides or {}

    for k, v in overrides.items():
        if k not in _THRESHOLD_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"Umbral desconocido '{k}'. Campos válidos: {sorted(_THRESHOLD_FIELDS)}",
            )
        if not isinstance(v, (int, float)) or v <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"El valor de '{k}' debe ser un número positivo.",
            )

    org.ai_thresholds_overrides = overrides
    db.add(org)
    await db.commit()
    await db.refresh(org)

    return AIThresholdsConfigResponse(
        thresholds=asdict(get_thresholds_for_org(org)),
        overrides=org.ai_thresholds_overrides or {},
        defaults=asdict(DEFAULT_THRESHOLDS),
    )


