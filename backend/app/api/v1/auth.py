from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.schemas.schemas import UserCreate, TokenResponse, UserLogin, UserResponse
from app.utils.security import (
    verify_password, get_password_hash, create_access_token, create_refresh_token,
    decode_token, revoke_jti, is_revoked, get_current_user,
    set_refresh_cookie, clear_refresh_cookie, REFRESH_COOKIE_NAME, RevocationCheckUnavailable,
)
from app.utils.throttle import enforce_rate_limit
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

# Slugs que colisionan con server_name reservados en Nginx (ver nginx/conf.d/).
RESERVED_ORG_SLUGS = {"api", "www", "admin", "localhost", "static"}


def get_client_ip(request: Request) -> str:
    """IP del cliente real detrás de un proxy (Nginx/túnel). No hay una lista
    de proxies confiables configurada (deployment simple), así que se toma el
    primer valor de X-Forwarded-For con fallback al socket directo — es una
    señal suficiente para limitar fuerza bruta, no para autorización."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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

    active_org = user.organizations[0].organization_id if user.organizations else None
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

    org = data.get("org") or (user.organizations[0].organization_id if user.organizations else None)

    # Rotación: revocar el refresh usado y emitir uno nuevo.
    await revoke_jti(data.get("jti"), settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60)
    new_refresh_token = create_refresh_token(subject=user.id, org=org)

    set_refresh_cookie(response, request, new_refresh_token)
    return {
        "access_token": create_access_token(subject=user.id, org=org),
        "token_type": "bearer",
        "user": enrich_user(user)
    }

@router.post("/logout")
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
