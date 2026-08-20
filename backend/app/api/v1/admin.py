"""Panel de administración de plataforma (execution/02).

**Qué hace y qué NO hace.** Estas rutas administran organizaciones: crearlas,
listarlas, cambiar su marca y prender o apagar sus módulos. Ninguna lee datos
de un tenant — ni campañas, ni gastos, ni conversaciones del chat.

Ese límite no es una omisión que haya que "completar más adelante": es lo que
mantiene en pie el aislamiento por organización, que es una de las reglas
innegociables del proyecto. Un rol que la saltee en silencio la deroga de
hecho. Si algún día hace falta soporte técnico con acceso a datos, eso es
impersonación explícita, con registro y con aviso al cliente.

El alta de un cliente **reusa la invitación que ya existe**: nadie escribe la
contraseña de nadie. Se crea la organización y se invita a su primer ADMIN, que
pone su propia clave desde el enlace.
"""
import datetime
import re
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ad_account import AdAccount
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.services.email_service import send_email
from app.tenant_rules.modules import (
    AVAILABLE_MODULES, UNDISABLEABLE_MODULES, enabled_modules_for, normalize_enabled_modules,
)
from app.utils.security import require_platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")

# Slugs que no pueden ser de un tenant porque ya significan otra cosa en el
# esquema de subdominios.
_RESERVED_SLUGS = frozenset({"www", "api", "admin", "app", "mail", "static", "cdn"})


class AdminOrganizationSummary(BaseModel):
    id: str
    slug: str
    name: str
    display_name: Optional[str] = None
    status: str
    industry: Optional[str] = None
    accent_color: Optional[str] = None
    enabled_modules: List[str]
    miembros: int
    cuentas_publicitarias: int
    created_at: datetime.datetime


class AdminOrganizationCreate(BaseModel):
    slug: str
    name: str
    display_name: Optional[str] = None
    industry: Optional[str] = None
    accent_color: Optional[str] = Field(default=None, pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
    enabled_modules: Optional[List[str]] = None
    # Email del primer ADMIN. Se le manda una invitación; no se le crea una
    # contraseña. Opcional: se puede dar de alta la organización y invitar
    # después.
    admin_email: Optional[EmailStr] = None


class AdminModulesUpdate(BaseModel):
    enabled_modules: List[str]


class AdminBrandUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    accent_color: Optional[str] = Field(default=None, pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
    industry: Optional[str] = None
    status: Optional[str] = None


async def _summary(db: AsyncSession, org: Organization) -> AdminOrganizationSummary:
    miembros = (await db.execute(
        select(func.count()).select_from(OrganizationUser)
        .where(OrganizationUser.organization_id == org.id)
    )).scalar() or 0
    cuentas = (await db.execute(
        select(func.count()).select_from(AdAccount)
        .where(AdAccount.organization_id == org.id, AdAccount.status != "ARCHIVED")
    )).scalar() or 0
    return AdminOrganizationSummary(
        id=org.id, slug=org.slug, name=org.name, display_name=org.display_name,
        status=org.status, industry=org.industry, accent_color=org.accent_color,
        enabled_modules=enabled_modules_for(org),
        miembros=int(miembros), cuentas_publicitarias=int(cuentas),
        created_at=org.created_at,
    )


@router.get("/modules")
async def list_available_modules(_: User = Depends(require_platform_admin)):
    """Catálogo de módulos. El frontend arma los checkboxes con esto en vez de
    tener su propia lista, que se desincronizaría."""
    return {"available": AVAILABLE_MODULES, "undisableable": sorted(UNDISABLEABLE_MODULES)}


@router.get("/organizations", response_model=List[AdminOrganizationSummary])
async def list_organizations(
    _: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lista las organizaciones con datos de ADMINISTRACIÓN.

    Se devuelven conteos —cuántos miembros, cuántas cuentas— y no contenido:
    nombres de campañas, gastos o conversaciones serían datos del tenant.
    """
    orgs = (await db.execute(select(Organization).order_by(Organization.created_at.desc()))).scalars().all()
    return [await _summary(db, o) for o in orgs]


@router.post("/organizations", response_model=AdminOrganizationSummary, status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: AdminOrganizationCreate,
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Da de alta un cliente y, si se indica email, invita a su primer ADMIN.

    La contraseña la pone el cliente desde el enlace de invitación. Este
    endpoint no crea credenciales de nadie.
    """
    slug = (payload.slug or "").strip().lower()
    if not _SLUG_RE.match(slug) or slug in _RESERVED_SLUGS:
        raise HTTPException(
            status_code=400,
            detail="El slug debe tener entre 3 y 40 caracteres (letras, números y guiones) y no puede ser un nombre reservado.",
        )
    if (await db.execute(select(Organization.id).where(Organization.slug == slug))).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya existe una organización con ese slug.")

    org = Organization(
        slug=slug,
        name=payload.name.strip(),
        display_name=(payload.display_name or "").strip() or None,
        industry=payload.industry,
        accent_color=payload.accent_color,
        # None y no lista vacía: `None` significa "todos habilitados", que es el
        # contrato del resto del sistema.
        enabled_modules=normalize_enabled_modules(payload.enabled_modules),
        status="ACTIVE",
    )
    db.add(org)
    await db.flush()

    if payload.admin_email:
        await _invite_first_admin(db, org, payload.admin_email, current_user)

    await db.commit()
    await db.refresh(org)
    return await _summary(db, org)


@router.patch("/organizations/{org_id}/modules", response_model=AdminOrganizationSummary)
async def update_organization_modules(
    org_id: str,
    payload: AdminModulesUpdate,
    _: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Prende y apaga módulos de un cliente.

    Vive acá y NO en `PATCH /organizations/my` a propósito: si el ADMIN de la
    organización pudiera cambiarlo, el módulo dejaría de ser una decisión
    comercial y pasaría a ser una preferencia del cliente.
    """
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    desconocidos = [m for m in payload.enabled_modules if m not in AVAILABLE_MODULES]
    if desconocidos:
        raise HTTPException(
            status_code=400,
            detail=f"Módulos desconocidos: {', '.join(sorted(desconocidos))}.",
        )
    org.enabled_modules = normalize_enabled_modules(payload.enabled_modules)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return await _summary(db, org)


@router.patch("/organizations/{org_id}", response_model=AdminOrganizationSummary)
async def update_organization(
    org_id: str,
    payload: AdminBrandUpdate,
    _: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Marca y estado de una organización. El slug NO se puede cambiar: es el
    subdominio del tenant y renombrarlo dejaría fuera a todos sus usuarios."""
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    if payload.status and payload.status not in ("ACTIVE", "SUSPENDED"):
        raise HTTPException(status_code=400, detail="Estado inválido.")

    for campo in ("name", "display_name", "accent_color", "industry", "status"):
        valor = getattr(payload, campo)
        if valor is not None:
            setattr(org, campo, valor)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return await _summary(db, org)


@router.post("/organizations/{org_id}/invite-admin", status_code=status.HTTP_201_CREATED)
async def invite_organization_admin(
    org_id: str,
    payload: Dict[str, Any],
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Invita al primer ADMIN de un cliente ya creado."""
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    email = (payload.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")

    token = await _invite_first_admin(db, org, email, current_user)
    await db.commit()
    return {"message": f"Invitación enviada a {email}.", "expires_in_days": 7, "token_creado": bool(token)}


async def _invite_first_admin(
    db: AsyncSession, org: Organization, email: str, invited_by: User
) -> str:
    """Crea la invitación de ADMIN y manda el correo.

    Reusa exactamente el mecanismo de invitaciones que ya existe —token de un
    solo uso con vencimiento— para no tener dos caminos distintos de alta de
    usuario que puedan divergir en seguridad.
    """
    ya_miembro = (await db.execute(
        select(User.id)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(User.email == email, OrganizationUser.organization_id == org.id)
    )).scalar_one_or_none()
    if ya_miembro:
        raise HTTPException(status_code=400, detail="Ese usuario ya es miembro de la organización.")

    token = secrets.token_urlsafe(32)
    db.add(Invitation(
        organization_id=org.id,
        email=email,
        role="ADMIN",
        token=token,
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=7),
        invited_by_user_id=invited_by.id,
    ))

    invite_url = f"http://{org.slug}.{settings.BASE_DOMAIN}/invitations/{token}"
    nombre = org.display_name or org.name
    await send_email(
        to_email=email,
        subject=f"Tu acceso a {nombre} en Agente DX IA",
        html_content=f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #333;">
        <h2>¡Hola!</h2>
        <p>Ya está listo el espacio de <strong>{nombre}</strong> en Agente DX IA.</p>
        <p>Entrá desde este enlace para elegir tu contraseña y empezar:</p>
        <p style="margin: 24px 0;">
            <a href="{invite_url}" style="background-color: #02b8cc; color: #0b0c0e; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                Activar mi acceso
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">Este enlace es válido por 7 días y se puede usar una sola vez.</p>
    </div>
    """,
        text_content=f"Tu acceso a {nombre} en Agente DX IA: {invite_url}",
    )
    return token
