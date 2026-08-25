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


async def has_role(db: AsyncSession, current_user: User, min_role: str) -> bool:
    """Verifica si el usuario tiene al menos `min_role` en su organización activa."""
    from sqlalchemy import select
    from app.models.organization_user import OrganizationUser

    org_id = current_user.organization_id
    if not org_id:
        return False

    q = select(OrganizationUser.role).where(
        OrganizationUser.user_id == current_user.id,
        OrganizationUser.organization_id == org_id,
    )
    role = (await db.execute(q)).scalar_one_or_none()
    return role is not None and ROLE_RANK.get(role, -1) >= ROLE_RANK.get(min_role, 0)


def require_role(min_role: str):
    async def _dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if not current_user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No perteneces a ninguna organización activa.",
            )
        if not await has_role(db, current_user, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta acción requiere el rol {min_role} o superior en la organización.",
            )
        return current_user

    return _dependency


async def require_platform_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Exige el flag de administrador de plataforma.

    Deliberadamente NO comprueba ningun rol de organizacion: son dos ejes
    distintos. Un ADMIN del Cliente A no puede crear el Cliente B, y un admin
    de plataforma no es ADMIN de ninguna organizacion por serlo.

    Y esto NO abre los datos de los tenants: las rutas que dependen de esta
    funcion administran organizaciones (crearlas, invitar a su primer ADMIN,
    configurar modulos), nunca leen sus campanas, gastos ni conversaciones. Ese
    limite es lo que mantiene en pie el aislamiento por organizacion.
    """
    if not getattr(current_user, "is_platform_admin", False):
        # 403 y no 404: el usuario esta autenticado y el recurso existe; lo que
        # falta es el permiso. Devolver 404 solo dificultaria diagnosticarlo.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta sección es solo para administradores de la plataforma.",
        )
    return current_user


def require_module(module: str):
    """Exige que la organización activa tenga habilitado `module`.

    Esta es la ÚNICA capa de las tres que es seguridad. Filtrar el menú y
    redirigir la ruta en el frontend son experiencia de usuario: sin esta
    dependencia el módulo está escondido, no apagado, y basta con llamar al
    endpoint a mano para seguir usándolo.

    Se responde 403 y no 404 a propósito: el cliente sabe que el módulo existe
    —se lo vendieron o no—, así que ocultar su existencia no protege nada y sí
    vuelve el problema imposible de diagnosticar.
    """
    async def _dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        from sqlalchemy import select
        from app.models.organization import Organization
        from app.tenant_rules.modules import AVAILABLE_MODULES, is_module_enabled

        if module not in AVAILABLE_MODULES:
            # Error de programación: un módulo fuera del catálogo nunca está
            # habilitado, así que dejarlo pasar sería abrir el endpoint a todos.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Módulo desconocido: {module}",
            )

        org_id = current_user.organization_id
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No perteneces a ninguna organización activa.",
            )

        org = (await db.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one_or_none()
        if not org or not is_module_enabled(org, module):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"El módulo '{AVAILABLE_MODULES[module]}' no está habilitado para tu organización.",
            )
        return current_user

    return _dependency


async def granted_ad_account_ids(
    db: AsyncSession, current_user: User, include_archived: bool = False
) -> list:
    """Las cuentas de la organización activa que ESTE usuario tiene concedidas.

    La organización sola no alcanza como unidad de aislamiento: una organización
    puede alojar las cuentas de varios clientes, y ser miembro era todo o nada
    —así es como el usuario de un cliente terminaba viendo las cuentas de los
    demás—. `user_ad_accounts` es la concesión explícita, y **fail-closed**: sin
    filas, ninguna cuenta.

    Se hace JOIN contra `ad_accounts` filtrando por organización, no un `SELECT
    ad_account_id FROM user_ad_accounts` a secas: una concesión vieja a una
    cuenta que ya no está en esta organización no puede sobrevivir como acceso.
    Los dos filtros valen, y el de organización sigue siendo el de afuera.

    Fuente ÚNICA de la respuesta a "qué cuentas ve este usuario": si cada
    endpoint arma su propio criterio, alcanza con que uno quede desalineado para
    reabrir el leak.
    """
    from sqlalchemy import select
    from app.models.ad_account import AdAccount
    from app.models.user_ad_account import UserAdAccount

    org_id = current_user.organization_id
    if not org_id:
        return []

    q = (
        select(AdAccount.id)
        .join(UserAdAccount, UserAdAccount.ad_account_id == AdAccount.id)
        .where(
            UserAdAccount.user_id == current_user.id,
            AdAccount.organization_id == org_id,
        )
    )
    if not include_archived:
        q = q.where(AdAccount.status != "ARCHIVED")
    return list((await db.execute(q)).scalars().all())


async def require_granted_ad_account(
    db: AsyncSession, current_user: User, ad_account_id: str, include_archived: bool = True
):
    """Devuelve la cuenta si el usuario la tiene concedida; si no, 404.

    404 y no 403 para no distinguir "no existe" de "no es tuya": es el mismo
    criterio que ya usaban estos endpoints al comparar la organización.
    """
    from sqlalchemy import select
    from app.models.ad_account import AdAccount
    from app.models.user_ad_account import UserAdAccount

    org_id = current_user.organization_id
    acc = (await db.execute(
        select(AdAccount)
        .join(UserAdAccount, UserAdAccount.ad_account_id == AdAccount.id)
        .where(
            AdAccount.id == ad_account_id,
            AdAccount.organization_id == org_id,
            UserAdAccount.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if acc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AdAccount no encontrada para esta organización.",
        )
    if not include_archived and acc.status == "ARCHIVED":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AdAccount no encontrada para esta organización.",
        )
    return acc


async def get_active_ad_account_id(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Optional[str]:
    """Resuelve la cuenta de anuncios activa de la request.

    Es el cuello por el que pasa todo el producto para saber sobre qué cuenta
    está trabajando, así que es acá donde se cierra el aislamiento por usuario:
    la cuenta no solo tiene que pertenecer a la organización, tiene que estar
    CONCEDIDA a quien pregunta (ver `granted_ad_account_ids`).

    - Header `X-Ad-Account-Id` presente: debe estar concedida (403 si no, sin
      distinguir "no existe" de "no es tuya" para no filtrar qué cuentas existen).
    - Sin header y 0 cuentas concedidas: None (los endpoints ya manejan "sin
      cuentas").
    - Sin header y exactamente 1 concedida: se auto-selecciona.
    - Sin header y 2+ concedidas: 400 explícito — elegir "la primera" en
      silencio es el bug que este mecanismo reemplaza.
    """
    from sqlalchemy import select
    from app.models.ad_account import AdAccount

    header_value = request.headers.get("X-Ad-Account-Id")

    # Las cuentas archivadas (soft-delete) no cuentan para la auto-selección ni
    # para el aviso de "elige una cuenta" — quedan fuera del uso normal.
    ids = await granted_ad_account_ids(db, current_user)

    if header_value:
        if header_value not in ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La cuenta de anuncios seleccionada no pertenece a esta organización.",
            )
        return header_value

    if not ids:
        # Dos situaciones que hasta ahora se veían iguales y ya no lo son:
        #
        #  - La organización no tiene NINGUNA cuenta → None, como siempre. Los
        #    endpoints que caen a "toda la organización" no devuelven nada
        #    igual, porque no hay cuentas de las que colgar datos.
        #
        #  - La organización SÍ tiene cuentas pero ninguna es de este usuario →
        #    403. Devolver None acá sería el leak: una docena de endpoints
        #    interpretan "sin cuenta activa" como "toda la organización", así
        #    que un usuario sin concesiones vería los datos de las cuentas
        #    ajenas. Y decirlo en voz alta es mejor que un panel vacío sin
        #    explicación: lo que hay que hacer es pedir acceso.
        hay_cuentas_en_la_org = (await db.execute(
            select(AdAccount.id).where(
                AdAccount.organization_id == current_user.organization_id,
                AdAccount.status != "ARCHIVED",
            ).limit(1)
        )).scalar_one_or_none()
        if hay_cuentas_en_la_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "NO_AD_ACCOUNTS_GRANTED",
                    "message": (
                        "No tienes ninguna cuenta publicitaria asignada. "
                        "Pide a un administrador de tu organización que te dé acceso."
                    ),
                },
            )
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
