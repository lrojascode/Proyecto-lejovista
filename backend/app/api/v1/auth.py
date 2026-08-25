import datetime
import hashlib
import ipaddress
import logging
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.password_reset import PasswordResetToken, RESET_TTL_MINUTES
from app.schemas.schemas import (
    UserCreate, TokenResponse, UserLogin, UserResponse, TenantBrandResponse,
    PasswordChangeRequest, PasswordForgotRequest, PasswordResetRequest,
    ProfileUpdateRequest,
)
from app.schemas.response_schemas import MessageResponse
from app.utils.security import (
    verify_password, get_password_hash, create_access_token, create_refresh_token,
    decode_token, revoke_jti, is_revoked, get_current_user,
    set_refresh_cookie, clear_refresh_cookie, REFRESH_COOKIE_NAME, RevocationCheckUnavailable,
)
from app.utils.throttle import enforce_rate_limit
from app.utils.urls import password_reset_url
from app.services.email_service import send_email
from app.config import settings

logger = logging.getLogger("lejovista.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# Slugs que colisionan con server_name reservados en Nginx (ver nginx/conf.d/).
RESERVED_ORG_SLUGS = {"api", "www", "admin", "localhost", "static"}


def get_client_ip(request: Request) -> str:
    """IP del cliente real detrás de un proxy (Nginx/túnel).

    (S-4) `X-Forwarded-For` es spoofeable por cualquiera que hable directo con
    el backend — antes se confiaba en el header sin condición. En este
    deployment el backend nunca se publica al host/internet (`expose`, no
    `ports`, en `docker-compose.prod.yml`; solo Nginx puede alcanzarlo dentro
    de la red de docker), así que el peer directo (`request.client.host`)
    siempre debería ser una IP privada de esa red interna. Se confía en el
    header SOLO cuando eso se cumple; si el peer directo fuera una IP pública
    (el backend quedó expuesto sin el proxy, por error de configuración), se
    ignora el header y se usa el peer directo — evita que cualquiera evada el
    rate limit por IP mandando un X-Forwarded-For inventado."""
    peer_ip = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer_ip and _is_trusted_proxy_peer(peer_ip):
        return forwarded.split(",")[0].strip()
    return peer_ip or "unknown"


def _is_trusted_proxy_peer(peer_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


async def resolve_active_org(
    request: Request, db: AsyncSession, user: User
) -> "str | None":
    """Con qué organización queda ATADA la sesión que se está emitiendo.

    Antes esto era `user.organizations[0].organization_id`: sin `ORDER BY` y sin
    mirar el subdominio, aunque el header viniera. Con un usuario que pertenece
    a dos organizaciones, cuál quedaba en el token variaba entre sesiones — así
    es como una API key terminó guardada en la organización equivocada y el
    analista contestó con plantillas durante días sin que nada se viera roto.

    Orden de decisión:
      1. `X-Tenant-Slug` (el subdominio): manda, y tiene que ser del usuario.
         Fallar acá es mejor que emitir un token de otra organización y que
         cada request posterior devuelva 403 sin explicar por qué.
      2. Una sola organización: esa.
      3. Varias y nada que lo fije: la membresía más ANTIGUA. No es una
         elección de negocio, es que sea siempre la misma.
    """
    memberships = list(user.organizations or [])
    if not memberships:
        return None

    tenant_slug = request.headers.get("X-Tenant-Slug")
    if tenant_slug:
        org = (await db.execute(
            select(Organization).where(Organization.slug == tenant_slug)
        )).scalar_one_or_none()
        propias = {ou.organization_id for ou in memberships}
        if not org or org.id not in propias:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acceso denegado: No tienes permisos para acceder a esta organización.",
            )
        return org.id

    if len(memberships) > 1:
        logger.warning(
            "El usuario %s pertenece a %d organizaciones y nada fijó cuál: se usa la "
            "membresía más antigua. Si los datos aparecen en la organización "
            "equivocada, es por acá.",
            user.email, len(memberships),
        )

    memberships.sort(
        key=lambda ou: (ou.created_at or datetime.datetime.min, ou.organization_id)
    )
    return memberships[0].organization_id


def enrich_user(user: User):
    # organization_id respeta el tenant activo de la sesión (property, S-8).
    active_org_id = user.organization_id
    org_role = "USER"
    if hasattr(user, 'organizations') and user.organizations:
        match = next(
            (ou for ou in user.organizations if ou.organization_id == active_org_id),
            user.organizations[0],
        )
        org_role = match.role

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": org_role,
        "organization_id": active_org_id or "",
        "created_at": user.created_at
    }

@router.post("/signup", response_model=TokenResponse)
async def signup(
    user_in: UserCreate, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    # Sin esto, cualquiera con la URL puede spamear registros (más relevante
    # una vez expuesta la app vía túnel/internet).
    await enforce_rate_limit("signup", get_client_ip(request), max_attempts=5, window_seconds=3600)

    if user_in.organization_slug.lower() in RESERVED_ORG_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese identificador de organización está reservado. Elige otro.",
        )

    # 1. Comprobar si el email ya existe
    q_email = select(User).where(User.email == user_in.email)
    res_email = await db.execute(q_email)
    if res_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El correo electrónico ya está registrado."
        )

    # 2. Comprobar si la organización slug ya existe. (S-1) Signup solo puede
    # FUNDAR una organización nueva: no existe flujo de invitación, así que
    # antes cualquiera que adivinara/conociera el slug de un tenant ajeno
    # (visible en el subdominio) podía registrarse contra él y el código de
    # abajo lo vinculaba como ADMIN de esa organización — toma de cuenta sin
    # autenticación. Sin invitaciones, unirse a una org existente por slug no
    # es una operación segura; se rechaza explícitamente.
    q_org = select(Organization).where(Organization.slug == user_in.organization_slug)
    res_org = await db.execute(q_org)
    org = res_org.scalar_one_or_none()

    if org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una organización con ese identificador. Pide que un administrador te invite.",
        )

    # Crear nueva organización (Tenant)
    org = Organization(
        slug=user_in.organization_slug,
        name=user_in.organization_name
    )
    db.add(org)
    await db.flush() # Para obtener el ID generado

    # 3. Crear usuario
    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name
    )
    db.add(user)
    await db.flush()
    
    # Vincular usuario a la organizacion
    org_user = OrganizationUser(
        user_id=user.id,
        organization_id=org.id,
        role="ADMIN"
    )
    db.add(org_user)
    
    await db.commit()

    # 4. Crear tokens JWT con la organización activa (S-8) + refresh (S-9)
    access_token = create_access_token(subject=user.id, org=org.id)
    refresh_token = create_refresh_token(subject=user.id, org=org.id)
    # Asignar la relación SIN disparar una carga lazy (que en async lanzaría
    # MissingGreenlet): fijamos el valor ya conocido como "committed".
    set_committed_value(user, "organizations", [org_user])

    set_refresh_cookie(response, request, refresh_token)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": enrich_user(user)
    }

@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: UserLogin, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    # Corta fuerza bruta de contraseña; permite ráfagas legítimas (typos).
    await enforce_rate_limit("login", get_client_ip(request), max_attempts=10, window_seconds=300)

    q = select(User).options(selectinload(User.organizations)).where(User.email == login_data.email)
    res = await db.execute(q)
    user = res.scalar_one_or_none()

    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos."
        )

    active_org = await resolve_active_org(request, db, user)
    access_token = create_access_token(subject=user.id, org=active_org)
    refresh_token = create_refresh_token(subject=user.id, org=active_org)

    set_refresh_cookie(response, request, refresh_token)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": enrich_user(user)
    }

@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Emite un nuevo par de tokens a partir del refresh token de la cookie
    httpOnly (S-2/S-9). Rota el refresh (revoca el anterior) para mitigar
    reuso de tokens robados. El JS del cliente nunca ve el refresh token —
    solo el navegador lo adjunta automáticamente vía cookie."""
    raw_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="No hay sesión activa (falta el refresh token).")

    data = decode_token(raw_token)
    if not data or data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Refresh token inválido.")
    try:
        if await is_revoked(data.get("jti")):
            raise HTTPException(status_code=401, detail="Refresh token revocado.")
    except RevocationCheckUnavailable:
        # (S-3) Fail-closed: sin forma de confirmar que este token NO esté
        # revocado, se rechaza la renovación en vez de aceptarla a ciegas.
        raise HTTPException(
            status_code=503,
            detail="No se pudo validar la sesión en este momento. Intenta de nuevo en unos segundos.",
        )

    user_id = data.get("sub")
    q = select(User).options(selectinload(User.organizations)).where(User.id == user_id)
    user = (await db.execute(q)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado.")

    # El claim del refresh manda, PERO se revalida: entre que se emitió y ahora,
    # al usuario lo pueden haber sacado de esa organización, y renovar la sesión
    # contra una organización de la que ya no es miembro sería seguir dándole
    # acceso por inercia. Si ya no vale, se resuelve de cero.
    propias = {ou.organization_id for ou in (user.organizations or [])}
    org = data.get("org") if data.get("org") in propias else None
    if org is None:
        org = await resolve_active_org(request, db, user)

    # Rotación: revocar el refresh usado y emitir uno nuevo.
    await revoke_jti(data.get("jti"), settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60)
    new_refresh_token = create_refresh_token(subject=user.id, org=org)

    set_refresh_cookie(response, request, new_refresh_token)
    return {
        "access_token": create_access_token(subject=user.id, org=org),
        "token_type": "bearer",
        "user": enrich_user(user)
    }

@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response):
    """Revoca el refresh token (S-9) y borra la cookie (S-2). Los access
    tokens caducan por expiración, no se revocan activamente."""
    raw_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_token:
        data = decode_token(raw_token)
        if data and data.get("jti"):
            await revoke_jti(data["jti"], settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60)
    clear_refresh_cookie(response, request)
    return {"message": "Sesión cerrada."}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return enrich_user(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Nombre y email propios. No había forma de corregir ninguno de los dos:
    un typo al aceptar la invitación quedaba para siempre."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()

    if payload.email is not None and payload.email.lower() != current_user.email.lower():
        nuevo = payload.email.lower()
        ocupado = (await db.execute(
            select(User.id).where(User.email == nuevo, User.id != current_user.id)
        )).scalar_one_or_none()
        if ocupado:
            raise HTTPException(status_code=400, detail="Ese correo ya está registrado.")
        current_user.email = nuevo

    db.add(current_user)
    await db.commit()
    return enrich_user(current_user)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cambio de contraseña con la sesión abierta.

    Exige la actual: si no, una sesión robada (o una pestaña abierta en una
    computadora ajena) alcanza para cambiar la clave y dejar afuera al dueño.
    """
    if not verify_password(payload.current_password, current_user.hashed_password):
        # Rate limit por usuario: este endpoint es un oráculo de contraseñas
        # para quien ya tiene la sesión.
        await enforce_rate_limit("change_password", current_user.id, max_attempts=5, window_seconds=300)
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")

    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="La contraseña nueva tiene que ser distinta de la actual.")

    current_user.hashed_password = get_password_hash(payload.new_password)
    db.add(current_user)
    await db.commit()

    # La sesión actual sigue viva —quien cambió la clave es quien está acá— pero
    # el refresh se rota: si el token viejo estaba comprometido, cambiar la
    # contraseña tiene que cortarlo, que es justo para lo que se cambia.
    viejo = request.cookies.get(REFRESH_COOKIE_NAME)
    if viejo:
        data = decode_token(viejo)
        if data and data.get("jti"):
            await revoke_jti(data["jti"], settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60)
    nuevo_refresh = create_refresh_token(
        subject=current_user.id, org=current_user.organization_id
    )
    set_refresh_cookie(response, request, nuevo_refresh)

    return {"message": "Contraseña actualizada."}


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    payload: PasswordForgotRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Envía un enlace de un solo uso para elegir contraseña nueva.

    **Responde lo mismo exista o no el email.** Distinguir los dos casos
    convierte este endpoint en un verificador de qué correos tienen cuenta, que
    es justo lo que busca quien prepara un ataque dirigido.
    """
    await enforce_rate_limit("forgot_password", get_client_ip(request), max_attempts=5, window_seconds=900)

    respuesta = {
        "message": (
            "Si ese correo tiene una cuenta, te llega un enlace para elegir una "
            "contraseña nueva. Revisá también el spam."
        )
    }

    user = (await db.execute(
        select(User).where(User.email == payload.email.lower())
    )).scalar_one_or_none()
    if not user:
        return respuesta

    # Los pedidos anteriores se invalidan: si alguien pide dos, el primero deja
    # de servir. Un enlace viejo dando vueltas en una casilla es una llave viva.
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=datetime.datetime.utcnow())
    )

    token_plano = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_reset_token(token_plano),
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=RESET_TTL_MINUTES),
    ))
    await db.commit()

    enlace = password_reset_url(token_plano)
    enviado = await send_email(
        to_email=user.email,
        subject="Recuperá tu contraseña",
        html_content=f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #333;">
        <h2>¡Hola!</h2>
        <p>Pediste elegir una contraseña nueva. El enlace vale por {RESET_TTL_MINUTES} minutos y una sola vez:</p>
        <p style="margin: 24px 0;">
            <a href="{enlace}" style="background-color: #02b8cc; color: #0b0c0e; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                Elegir contraseña nueva
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">Si no fuiste vos, ignorá este correo: tu contraseña no cambia sola.</p>
    </div>
    """,
        text_content=f"Elegí una contraseña nueva ({RESET_TTL_MINUTES} min): {enlace}",
    )
    if not enviado:
        # No se le dice al usuario —seguiría siendo un oráculo— pero tiene que
        # quedar en el log: sin proveedor de correo, este flujo no lleva a nada.
        logger.error(
            "Recuperación de contraseña para %s: el correo NO se pudo enviar. "
            "Sin BREVO_API_KEY el enlace no llega a nadie.", user.email,
        )
    return respuesta


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    payload: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Consume el enlace y fija la contraseña nueva."""
    await enforce_rate_limit("reset_password", get_client_ip(request), max_attempts=10, window_seconds=900)

    fila = (await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == _hash_reset_token(payload.token)
        )
    )).scalar_one_or_none()
    if fila is None or not fila.is_usable:
        raise HTTPException(
            status_code=400,
            detail="Ese enlace ya no sirve (se usó o venció). Pedí uno nuevo.",
        )

    user = await db.get(User, fila.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Ese enlace ya no sirve. Pedí uno nuevo.")

    user.hashed_password = get_password_hash(payload.new_password)
    fila.used_at = datetime.datetime.utcnow()
    db.add_all([user, fila])
    await db.commit()

    return {"message": "Listo. Ya podés entrar con tu contraseña nueva."}


def _hash_reset_token(token: str) -> str:
    """SHA-256 del token. Lo que se guarda no puede servir para entrar: la tabla
    es un objetivo tan bueno como `users`."""
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/tenant-brand", response_model=TenantBrandResponse)
async def get_tenant_brand(
    request: Request,
    slug: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Devuelve la información de marca (nombre, logo, color) del tenant activo
    resuelto por subdominio (X-Tenant-Slug) o query param. Si no hay tenant resuelto
    o no existe, devuelve la marca neutra de LejoVista (nunca la de otro cliente)."""
    tenant_slug = slug or request.headers.get("X-Tenant-Slug")
    if not tenant_slug or tenant_slug in RESERVED_ORG_SLUGS:
        return TenantBrandResponse(
            name="LejoVista",
            display_name="LejoVista",
            logo_url=None,
            accent_color=None,
        )

    q = select(Organization).where(Organization.slug == tenant_slug)
    org = (await db.execute(q)).scalar_one_or_none()
    if not org:
        return TenantBrandResponse(
            name="LejoVista",
            display_name="LejoVista",
            logo_url=None,
            accent_color=None,
        )

    return TenantBrandResponse(
        name=org.name,
        display_name=org.display_name or org.name,
        logo_url=org.logo_url,
        accent_color=org.accent_color,
    )
