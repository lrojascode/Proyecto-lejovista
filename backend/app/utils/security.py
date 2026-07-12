from datetime import datetime, timedelta
from typing import Optional, Union, Any, Dict
import jwt
import uuid
import logging
import redis.asyncio as aioredis
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request, Response
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.models.user import User

logger = logging.getLogger("lejovista.security")

# Usamos bcrypt para hashes de contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)

# (S-2) El refresh token viaja como cookie httpOnly — nunca en el cuerpo JSON
# ni en localStorage — para que una vulnerabilidad XSS no pueda leerlo con
# `document.cookie` ni con JS en general.
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _refresh_cookie_domain(request: Request) -> Optional[str]:
    """El `Domain` de la cookie debe abarcar los dos subdominios (frontend en
    `<slug>.BASE_DOMAIN`, API en `api.BASE_DOMAIN`) SOLO cuando el host real de
    la request pertenece a ese esquema. En modo túnel/single-host (Nginx
    reenvía /api bajo el mismo hostname que el frontend, sin relación con
    BASE_DOMAIN) fijar un Domain explícito que no coincide con el host visto
    haría que el navegador rechace la cookie entera — mejor no fijar Domain y
    dejar que aplique solo al host exacto (funciona igual, es el mismo origen)."""
    base_host = settings.BASE_DOMAIN.split(":")[0]
    host = (request.headers.get("host") or "").split(":")[0]
    if base_host and (host == base_host or host.endswith(f".{base_host}")):
        return base_host
    return None


def _is_secure_request(request: Request) -> bool:
    """Nginx reenvía X-Forwarded-Proto (ver nginx/conf.d/*.conf) — la conexión
    real del navegador puede ser HTTPS aunque uvicorn solo vea HTTP plano
    detrás del proxy. En local (sin proxy) esto cae a request.url.scheme."""
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )


def set_refresh_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        path=REFRESH_COOKIE_PATH,
        domain=_refresh_cookie_domain(request),
        secure=_is_secure_request(request),
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        domain=_refresh_cookie_domain(request),
    )

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(subject: Union[str, Any], org: Optional[str] = None, expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {"exp": expire, "sub": str(subject), "type": "access", "jti": str(uuid.uuid4())}
    if org:
        # Organización "activa" del usuario para esta sesión (multi-tenant, S-8).
        to_encode["org"] = str(org)
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt


def create_refresh_token(subject: Union[str, Any], org: Optional[str] = None) -> str:
    """Token de refresco de larga duración (S-9). Revocable vía blocklist por jti."""
    expire = datetime.utcnow() + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh", "jti": str(uuid.uuid4())}
    if org:
        to_encode["org"] = str(org)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None


async def revoke_jti(jti: str, ttl_seconds: int) -> None:
    """Marca un jti como revocado en Redis (para logout / rotación de refresh)."""
    if not jti:
        return
    client = aioredis.Redis.from_url(settings.REDIS_URL)
    try:
        await client.set(f"revoked:{jti}", "1", ex=max(ttl_seconds, 1))
    except Exception as e:
        logger.warning("No se pudo revocar jti %s (Redis no disponible): %s", jti, e)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


class RevocationCheckUnavailable(Exception):
    """(S-3) Redis no respondió al consultar la blocklist. Distinta de "no
    revocado": el llamador debe decidir fail-closed (rechazar) para el
    refresh, en vez de asumir silenciosamente que el token sigue siendo válido."""


async def is_revoked(jti: str) -> bool:
    """True si el jti fue revocado.

    (S-3) Fail-**closed**: si Redis no responde, no se puede afirmar que el
    token NO esté revocado, así que se levanta `RevocationCheckUnavailable`
    en vez de devolver `False` — el único llamador (`POST /auth/refresh`)
    rechaza la renovación de sesión mientras dure la caída, en vez de aceptar
    un refresh potencialmente robado/rotado porque no había forma de
    verificarlo. El resto de mecanismos basados en Redis (rate limiting,
    cache, debounce) siguen fail-open a propósito — solo la validez de la
    sesión amerita el trade-off de disponibilidad por seguridad.
    """
    if not jti:
        return False
    client = aioredis.Redis.from_url(settings.REDIS_URL)
    try:
        return bool(await client.get(f"revoked:{jti}"))
    except Exception as e:
        logger.warning("No se pudo verificar revocación de jti %s (Redis no disponible): %s", jti, e)
        raise RevocationCheckUnavailable() from e
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudieron validar las credenciales de acceso.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    user_id = None
    
    # 1. Intentar validar usando token JWT en headers
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("sub")
        except Exception:
            raise credentials_exception

    if not user_id:
        raise credentials_exception
        
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    q_user = select(User).options(selectinload(User.organizations)).where(User.id == user_id)
    res_user = await db.execute(q_user)
    user = res_user.scalar_one_or_none()
    
    if user is None:
        raise credentials_exception
        
    # Determinar la organización ACTIVA de la sesión (S-8) y validar aislamiento.
    user_org_ids = [ou.organization_id for ou in user.organizations] if user.organizations else []
    active_org_id = None

    tenant_slug = request.headers.get("X-Tenant-Slug")
    if tenant_slug:
        # El subdominio elige el tenant activo; debe pertenecer al usuario.
        from app.models.organization import Organization
        q_org = select(Organization).where(Organization.slug == tenant_slug)
        res_org = await db.execute(q_org)
        org = res_org.scalar_one_or_none()
        if not org or org.id not in user_org_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acceso denegado: No tienes permisos para acceder a esta organización."
            )
        active_org_id = org.id
    else:
        # Sin subdominio: usar la organización del claim del token si es válida.
        token_org = payload.get("org")
        if token_org and token_org in user_org_ids:
            active_org_id = token_org

    if active_org_id:
        # Atributo no mapeado leído por User.organization_id (multi-tenant real).
        user._active_org_id = active_org_id

    return user


# (S-1) Jerarquía de roles de OrganizationUser.role: ADMIN por encima de
# OPERATOR por encima de VIEWER. `require_role(min_role)` exige ese rol o uno
# de rango superior en la organización ACTIVA de la sesión (misma resolución
# de tenant que get_current_user/S-8) — antes el rol se guardaba pero ningún
# endpoint lo verificaba, así que un VIEWER tenía en la práctica permisos de
# ADMIN.
ROLE_RANK = {"VIEWER": 0, "OPERATOR": 1, "ADMIN": 2}


def require_role(min_role: str):
    async def _dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        from sqlalchemy import select
        from app.models.organization_user import OrganizationUser

        org_id = current_user.organization_id
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No perteneces a ninguna organización activa.",
            )

        q = select(OrganizationUser.role).where(
            OrganizationUser.user_id == current_user.id,
            OrganizationUser.organization_id == org_id,
        )
        role = (await db.execute(q)).scalar_one_or_none()

        if role is None or ROLE_RANK.get(role, -1) < ROLE_RANK[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta acción requiere el rol {min_role} o superior en la organización.",
            )
        return current_user

    return _dependency


async def get_active_ad_account_id(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Optional[str]:
    """Resuelve la cuenta de anuncios activa de la request (aislamiento entre
    varias cuentas de una misma organización).

    - Header `X-Ad-Account-Id` presente: debe pertenecer a la organización del
      usuario (403 si no, sin distinguir "no existe" de "es de otra org" para no
      filtrar qué cuentas existen).
    - Sin header y la org tiene 0 cuentas: None (los endpoints ya manejan "sin
      cuentas").
    - Sin header y la org tiene exactamente 1 cuenta: se auto-selecciona (preserva
      el comportamiento actual para orgs con una sola cuenta conectada).
    - Sin header y la org tiene 2+ cuentas: 400 explícito — elegir "la primera" en
      silencio es el bug que este mecanismo reemplaza.
    """
    from sqlalchemy import select
    from app.models.ad_account import AdAccount

    org_id = current_user.organization_id
    header_value = request.headers.get("X-Ad-Account-Id")

    if header_value:
        q = select(AdAccount).where(
            AdAccount.id == header_value,
            AdAccount.organization_id == org_id,
            AdAccount.status != "ARCHIVED",
        )
        res = await db.execute(q)
        if not res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La cuenta de anuncios seleccionada no pertenece a esta organización.",
            )
        return header_value

    # Las cuentas archivadas (soft-delete) no cuentan para la auto-selección ni
    # para el aviso de "elige una cuenta" — quedan fuera del uso normal.
    q = select(AdAccount.id).where(
        AdAccount.organization_id == org_id,
        AdAccount.status != "ARCHIVED",
    )
    res = await db.execute(q)
    ids = res.scalars().all()

    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "MULTIPLE_AD_ACCOUNTS_NO_SELECTION",
            "message": "Esta organización tiene múltiples cuentas de anuncios; selecciona una.",
        },
    )
