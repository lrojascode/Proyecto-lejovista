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
import logging
import re
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ad_account import AdAccount
from app.models.invitation import Invitation, hash_invitation_token
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.services.email_service import send_email
from app.services.offboarding import (
    DIAS_PARA_ELIMINAR, BajaInvalida, certificado_de_eliminacion, eliminar_datos,
    exportar_datos, iniciar_baja,
)
from app.services.onboarding import (
    ESTADO_OMITIDO, ESTADO_VALIDADO, PASOS_REQUERIDOS, VALIDADORES,
    AltaIncompleta, PasoNoValidado, alta_completa, pasos_faltantes, registrar_paso,
)
from app.tenant_rules.modules import (
    AVAILABLE_MODULES, UNDISABLEABLE_MODULES, enabled_modules_for, normalize_enabled_modules,
)
from app.utils.urls import invitation_url
from app.utils.security import require_platform_admin

logger = logging.getLogger("lejovista.admin")

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
    # Solo presente cuando el alta creó una invitación. Trae el enlace para
    # poder pasarlo a mano si el correo no está configurado.
    invitacion: Optional[Dict[str, Any]] = None


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

    invitacion = None
    if payload.admin_email:
        # El alta es una SECUENCIA, no un formulario (spec 05): crear la
        # organización e invitar al cliente en el mismo paso es lo que permitía
        # que entrara a un espacio sin Meta, sin IA y sin WhatsApp. El email se
        # guarda como intención y la invitación se emite cuando el alta esté
        # completa.
        org.onboarding_state = {
            **(org.onboarding_state or {}),
            "admin_email_previsto": payload.admin_email.strip(),
        }
        db.add(org)

    await db.commit()
    await db.refresh(org)
    resumen = await _summary(db, org)
    resumen.invitacion = invitacion
    return resumen


@router.post("/organizations/{org_id}/offboarding/iniciar")
async def iniciar_baja_de_cliente(
    org_id: str,
    payload: Dict[str, Any],
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Corta todos los accesos del cliente, ya. **No borra nada.**

    Es la fase inmediata de la baja (spec 09): revoca las credenciales de Meta,
    elimina la clave de IA, invalida las sesiones de los miembros y bloquea
    login, API, sync, IA y write-back.

    La eliminación de datos es otra operación, a los 30 días, para que el cliente
    pueda exportar. Juntarlas obligaría a elegir entre cortar tarde o borrar sin
    dar tiempo.

    Exige confirmar el identificador de la organización: es la clase de operación
    que no se puede lanzar por un clic en la fila equivocada.
    """
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    if (payload.get("confirmar_slug") or "").strip() != org.slug:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "OFFBOARDING_CONFIRMATION_REQUIRED",
                "message": (
                    f"Para dar de baja a '{org.name}' hay que confirmar su "
                    f"identificador ('{org.slug}') en `confirmar_slug`."
                ),
            },
        )
    motivo = (payload.get("motivo") or "").strip()
    if len(motivo) < 5:
        raise HTTPException(
            status_code=400,
            detail="Hace falta un motivo: una baja sin explicación es una baja sin auditoría.",
        )

    try:
        resultado = await iniciar_baja(db, org, actor_id=current_user.id, motivo=motivo)
    except BajaInvalida as e:
        raise HTTPException(status_code=400, detail=e.as_detail())

    await db.commit()
    await db.refresh(org)
    return {
        "fase": resultado.fase,
        "detalle": resultado.detalle,
        "eliminar_a_partir_de": (org.offboarding_state or {}).get("eliminar_a_partir_de"),
        "dias_para_exportar": DIAS_PARA_ELIMINAR,
    }


@router.get("/organizations/{org_id}/offboarding/exportacion")
async def exportar_datos_del_cliente(
    org_id: str,
    _: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Los datos comerciales del cliente, para que se los lleve.

    Sin secretos, sin identificadores internos y sin nada de otras
    Organizaciones: lo que se lleva es su histórico, no nuestra base de datos.
    """
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    return await exportar_datos(db, org)


@router.post("/organizations/{org_id}/offboarding/eliminar")
async def eliminar_datos_del_cliente(
    org_id: str,
    payload: Dict[str, Any],
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Elimina histórico, medios y datos de campañas. **Irreversible.**

    Alcanza también a las tablas de archivo, que se diseñaron sin claves foráneas
    para sobrevivir al borrado de un tenant: omitirlas convertiría la promesa de
    eliminación en falsa.

    Se niega antes del plazo comunicado salvo `forzar` explícito. Y exige un
    respaldo externo verificado inmediatamente antes: el único rollback de esta
    operación es una restauración.
    """
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    if (payload.get("confirmar_slug") or "").strip() != org.slug:
        raise HTTPException(
            status_code=400,
            detail={"code": "OFFBOARDING_CONFIRMATION_REQUIRED",
                    "message": f"Confirma el identificador '{org.slug}' en `confirmar_slug`."},
        )
    if not payload.get("respaldo_verificado"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "OFFBOARDING_BACKUP_REQUIRED",
                "message": (
                    "Esta operación no tiene rollback salvo restaurar desde un "
                    "respaldo. Verifica uno externo (scripts/backup/verify.sh) y "
                    "marca `respaldo_verificado`."
                ),
            },
        )

    try:
        resultado = await eliminar_datos(db, org, forzar=bool(payload.get("forzar")))
    except BajaInvalida as e:
        raise HTTPException(status_code=400, detail=e.as_detail())

    await db.commit()
    await db.refresh(org)
    return {
        "fase": resultado.fase,
        "detalle": resultado.detalle,
        "certificado": certificado_de_eliminacion(org),
    }


@router.get("/organizations/{org_id}/offboarding/certificado")
async def certificado_de_baja(
    org_id: str,
    _: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lo que el cliente recibe: qué se eliminó, qué se conserva y por cuánto."""
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    try:
        return certificado_de_eliminacion(org)
    except BajaInvalida as e:
        raise HTTPException(status_code=400, detail=e.as_detail())


@router.get("/organizations/{org_id}/onboarding")
async def estado_del_alta(
    org_id: str,
    _: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Qué pasos del alta están resueltos y cuáles faltan."""
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    faltantes = pasos_faltantes(org)
    return {
        "organization_id": org.id,
        "pasos_requeridos": list(PASOS_REQUERIDOS),
        "estado": org.onboarding_state or {},
        "faltantes": faltantes,
        "completa": not faltantes,
        "puede_invitar": not faltantes,
    }


@router.post("/organizations/{org_id}/onboarding/{paso}")
async def validar_paso_del_alta(
    org_id: str,
    paso: str,
    payload: Optional[Dict[str, Any]] = None,
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Valida un paso del alta contra el sistema externo REAL.

    No comprueba que el campo esté lleno: comprueba que funcione. Un token de
    Meta inválido se guarda igual de bien que uno bueno, y la diferencia solo
    aparece en el primer sync — con el cliente ya dentro.

    Con `{"omitir": true, "motivo": "..."}` el paso se marca OMITIDO. Queda
    registrado y es distinto de "validado": hacer el hueco explícito evita
    dejarlo en silencio.
    """
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")
    if paso not in VALIDADORES:
        raise HTTPException(
            status_code=400,
            detail=f"Paso desconocido: {paso}. Válidos: {', '.join(PASOS_REQUERIDOS)}.",
        )

    cuerpo = payload or {}
    if cuerpo.get("omitir"):
        motivo = (cuerpo.get("motivo") or "").strip()
        if len(motivo) < 5:
            raise HTTPException(
                status_code=400,
                detail="Para omitir un paso hace falta un motivo. Un hueco sin explicación es un hueco olvidado.",
            )
        registrar_paso(org, paso, estado=ESTADO_OMITIDO, detalle=motivo, actor_id=current_user.id)
    else:
        try:
            detalle = await VALIDADORES[paso](db, org)
        except PasoNoValidado as e:
            raise HTTPException(status_code=400, detail=e.as_detail())
        registrar_paso(org, paso, estado=ESTADO_VALIDADO, detalle=detalle, actor_id=current_user.id)

    db.add(org)
    await db.commit()
    await db.refresh(org)
    faltantes = pasos_faltantes(org)
    return {
        "paso": paso,
        "estado": (org.onboarding_state or {}).get(paso),
        "faltantes": faltantes,
        "puede_invitar": not faltantes,
    }


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

    suspendiendo = payload.status == "SUSPENDED" and org.status != "SUSPENDED"

    for campo in ("name", "display_name", "accent_color", "industry", "status"):
        valor = getattr(payload, campo)
        if valor is not None:
            setattr(org, campo, valor)
    db.add(org)

    if suspendiendo:
        await _revocar_credenciales_meta(db, org.id)
        # Las sesiones vivas de los miembros se cortan además por el corte por
        # usuario: sin esto, un access token ya emitido seguiría entrando hasta
        # 24 horas después de la suspensión. La compuerta de `get_current_user`
        # lo bloquearía igual, pero cortar la credencial es lo que hace que la
        # suspensión sea efectiva y no solo visible.
        await _revocar_sesiones_de_la_organizacion(db, org.id)

    await db.commit()
    await db.refresh(org)
    return await _summary(db, org)


async def _revocar_credenciales_meta(db: AsyncSession, org_id: str) -> int:
    """Marca como REVOKED las credenciales de Meta de una organización.

    Se ejecuta al suspender: mientras la credencial siga ACTIVE, cualquier
    camino que la lea puede seguir hablando con Meta en nombre de un cliente al
    que ya se le cortó el servicio.

    El token cifrado NO se borra acá: la suspensión es reversible y borrarlo
    obligaría a reconectar Meta a mano para reactivar. La eliminación definitiva
    pertenece a la baja de la organización, que es otra operación.
    """
    from app.models.meta_credential import MetaCredential

    res = await db.execute(
        update(MetaCredential)
        .where(
            MetaCredential.organization_id == org_id,
            MetaCredential.status != "REVOKED",
        )
        .values(status="REVOKED", updated_at=datetime.datetime.utcnow())
    )
    return res.rowcount or 0


async def _revocar_sesiones_de_la_organizacion(db: AsyncSession, org_id: str) -> int:
    """Invalida todas las credenciales vivas de los miembros de la organización."""
    from app.utils.security import session_cutoff_now

    miembros = (await db.execute(
        select(OrganizationUser.user_id).where(
            OrganizationUser.organization_id == org_id
        )
    )).scalars().all()
    if not miembros:
        return 0
    res = await db.execute(
        update(User)
        .where(User.id.in_(list(miembros)))
        .values(sessions_valid_from=session_cutoff_now())
    )
    return res.rowcount or 0


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
    email = (payload.get("email") or "").strip() or (org.onboarding_state or {}).get("admin_email_previsto", "")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")

    # La compuerta del alta asistida: la invitación no sale hasta que Meta, IA y
    # WhatsApp estén resueltos (spec 05, criterios 10 y 11).
    if not alta_completa(org):
        raise HTTPException(status_code=400, detail=AltaIncompleta(pasos_faltantes(org)).as_detail())

    invitacion = await _invite_first_admin(db, org, email, current_user)
    await db.commit()
    return invitacion


async def _invite_first_admin(
    db: AsyncSession, org: Organization, email: str, invited_by: User
) -> Dict[str, Any]:
    """Crea la invitación de ADMIN y manda el correo.

    Reusa exactamente el mecanismo de invitaciones que ya existe —token de un
    solo uso con vencimiento— para no tener dos caminos distintos de alta de
    usuario que puedan divergir en seguridad.

    Devuelve además el ENLACE y si el correo salió de verdad. `send_email` es
    best-effort: sin `BREVO_API_KEY` configurada registra el envío en el log y
    devuelve éxito sin mandar nada. Sin esto, el alta respondía "invitación
    enviada" mientras el cliente no recibía absolutamente nada — y no había
    forma de enterarse. Con el enlace en la respuesta, quien da de alta puede
    pasarlo por otro medio.
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
        token_hash=hash_invitation_token(token),
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=7),
        invited_by_user_id=invited_by.id,
    ))

    invite_url = invitation_url(token)
    nombre = org.display_name or org.name
    enviado = await send_email(
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
    correo_configurado = bool(settings.BREVO_API_KEY) and not settings.MOCK_SERVICES
    if not correo_configurado:
        logger.warning(
            "Invitación de %s creada SIN enviar correo (BREVO_API_KEY sin configurar). "
            "Hay que pasarle el enlace a mano.", email,
        )
    return {
        "email": email,
        "invite_url": invite_url,
        "expira_en_dias": 7,
        # Distingue "se mandó" de "se creó y nadie lo mandó". Son cosas muy
        # distintas para quien está dando de alta a un cliente.
        "correo_enviado": bool(enviado) and correo_configurado,
    }
