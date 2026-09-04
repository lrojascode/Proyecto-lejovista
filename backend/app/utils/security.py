from datetime import datetime, timedelta, timezone
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

# Tipos de credencial. Cada superficie declara cual acepta y lo comprueba ANTES
# de resolver el sujeto: un refresh (7 dias) presentado como Bearer abria la API
# entera con la credencial de mayor vida del sistema.
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
TOKEN_TYPE_WS_TICKET = "ws_ticket"

# Vida del ticket de WebSocket: lo justo para ir del `fetch` que lo pide al
# handshake que lo canjea.
WS_TICKET_EXPIRE_SECONDS = 30


def _base_claims(subject: Union[str, Any], token_type: str, expire: datetime) -> Dict[str, Any]:
    """Claims comunes a toda credencial emitida.

    `iat` es el que permite revocar en bloque: con el corte por usuario
    (`User.sessions_valid_from`) no hace falta enumerar jti para expulsar todas
    las sesiones, basta comparar cuando se emitio cada token.
    """
    ahora = datetime.utcnow()
    return {
        "exp": expire,
        "iat": ahora,
        # `iat` estandar viaja en SEGUNDOS enteros, y con esa resolucion el token
        # que se emite justo despues de un cambio de contrasena puede caer en el
        # mismo segundo que el corte y no distinguirse de los que hay que
        # expulsar. `iat_ms` da la resolucion que hace falta para que el corte
        # sea exacto; `iat` se conserva por ser el claim estandar.
        # `.replace(tzinfo=utc)` NO es decorativo: `datetime.utcnow()` devuelve un
        # naive, y `.timestamp()` sobre un naive lo interpreta como hora LOCAL.
        # Sin esto el desfase horario del servidor se colaba en la comparacion y
        # el corte no rechazaba nada.
        "iat_ms": int(ahora.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "sub": str(subject),
        "type": token_type,
        "jti": str(uuid.uuid4()),
    }


def create_access_token(subject: Union[str, Any], org: Optional[str] = None, expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = _base_claims(subject, TOKEN_TYPE_ACCESS, expire)
    if org:
        # Organización "activa" del usuario para esta sesión (multi-tenant, S-8).
        to_encode["org"] = str(org)
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt


def create_refresh_token(subject: Union[str, Any], org: Optional[str] = None) -> str:
    """Token de refresco de larga duración (S-9). Revocable vía blocklist por jti."""
    expire = datetime.utcnow() + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode = _base_claims(subject, TOKEN_TYPE_REFRESH, expire)
    if org:
        to_encode["org"] = str(org)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")


def create_ws_ticket(subject: Union[str, Any], org: Optional[str] = None) -> str:
    """Credencial de un solo uso para abrir un WebSocket.

    Existe para sacar el access token del query string: la URL de un WebSocket
    la registran el proxy, los historiales y cualquier intermediario, y ahi
    quedaba una credencial de 24 horas. El ticket vale 30 segundos, sirve solo
    para el handshake y se consume al canjearlo.
    """
    expire = datetime.utcnow() + timedelta(seconds=WS_TICKET_EXPIRE_SECONDS)
    to_encode = _base_claims(subject, TOKEN_TYPE_WS_TICKET, expire)
    if org:
        to_encode["org"] = str(org)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str, expected_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Decodifica y valida la firma. Con `expected_type`, exige ademas ese tipo.

    Un tipo inesperado devuelve `None`, igual que una firma invalida: desde
    afuera las dos situaciones tienen que ser indistinguibles, o el error
    mismo dice que la credencial es autentica pero de otra clase.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None
    if expected_type is not None and payload.get("type") != expected_type:
        return None
    return payload


def token_predates_session_cutoff(payload: Dict[str, Any], user: "User") -> bool:
    """True si este token se emitio antes del corte de sesiones del usuario.

    Sin `iat` no se puede probar que sea posterior al corte, asi que se rechaza:
    son los tokens anteriores al despliegue de este mecanismo, y solo afectan a
    usuarios que ademas cambiaron su contrasena.
    """
    cutoff = getattr(user, "sessions_valid_from", None)
    if not cutoff:
        return False

    crudo = payload.get("iat_ms")
    divisor = 1000.0
    if crudo is None:
        # Tokens anteriores al despliegue de `iat_ms`: se cae al claim estandar,
        # con su resolucion de segundos.
        crudo = payload.get("iat")
        divisor = 1.0
    if crudo is None:
        return True
    try:
        emitido = datetime.utcfromtimestamp(int(crudo) / divisor)
    except (TypeError, ValueError, OSError, OverflowError):
        return True
    return emitido < cutoff


def session_cutoff_now() -> datetime:
    """Instante de corte para revocar todas las sesiones de un usuario.

    Truncado al milisegundo, que es la resolucion de `iat_ms`. Sin truncar, el
    token que se emite inmediatamente despues del corte podria quedar por
    debajo de el al redondear su marca hacia abajo, y se rechazaria a si mismo:
    cambiar la contrasena te dejaria fuera de tu propia sesion.
    """
    ahora = datetime.utcnow()
    return ahora.replace(microsecond=(ahora.microsecond // 1000) * 1000)


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


async def consume_ws_ticket(jti: Optional[str]) -> bool:
    """Marca un ticket de WebSocket como usado. True si era la primera vez.

    `SET NX` con expiración: la operación de comprobar-y-marcar es una sola, así
    que dos handshakes simultáneos con el mismo ticket no pueden pasar los dos.

    Fail-**open** deliberado: si el cache no responde no se puede garantizar la
    unicidad, pero el ticket ya vive 30 segundos y dejar sin notificaciones a
    todo el producto por una caída del soporte es peor que aceptar un reuso
    dentro de esa ventana. Lo que no es fail-open es la validez del ticket en sí
    —firma, tipo, vencimiento, corte de sesiones y pertenencia— que se comprueba
    igual.
    """
    if not jti:
        return False
    client = aioredis.Redis.from_url(settings.REDIS_URL)
    try:
        return bool(await client.set(f"ws_ticket:{jti}", "1", nx=True, ex=WS_TICKET_EXPIRE_SECONDS * 2))
    except Exception as e:
        logger.warning("No se pudo marcar el ticket de WebSocket %s como usado: %s", jti, e)
        return True
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


class OrganizationSuspended(HTTPException):
    """La organización activa de la sesión no está ACTIVE.

    Se responde 403 y no 401: la credencial es válida y la sesión existe; lo que
    no está habilitado es la organización. Distinguirlas importa porque el
    frontend tiene que mostrar "tu organización está suspendida" y no mandar al
    usuario a reintentar el login, que fallaría igual.
    """

    def __init__(self, org_status: str):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORGANIZATION_SUSPENDED",
                "message": (
                    "Esta organización está suspendida. Contacta con la agencia "
                    "para reactivarla."
                ),
                "status": org_status,
            },
        )


async def load_user_with_organizations(db: AsyncSession, user_id: str) -> Optional[User]:
    """El usuario con sus membresías Y la Organization de cada una.

    Se traen las organizaciones enteras y no solo sus ids porque toda request
    necesita ahora el `status` de la organización activa (suspensión efectiva).
    Cargarlas de una vez cuesta una consulta más por request y evita una
    segunda consulta puntual por cada comprobación.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.organization_user import OrganizationUser

    q = (
        select(User)
        .options(
            selectinload(User.organizations).selectinload(OrganizationUser.organization)
        )
        .where(User.id == user_id)
    )
    return (await db.execute(q)).scalar_one_or_none()


def resolve_active_organization(request: Request, user: User, payload: Dict[str, Any]):
    """La Organization activa de esta request, o None.

    Mismo orden de decisión que antes (S-8): manda el subdominio, y si no lo
    hay, el claim del token. Lo que cambia es que se resuelve contra las
    organizaciones YA cargadas del usuario en vez de consultar por slug: si el
    slug no es de ninguna de ellas, no hay nada que mirar, y así una petición
    con el slug de otro tenant no llega ni a leer su fila.
    """
    memberships = list(user.organizations or [])
    por_id = {ou.organization_id: ou.organization for ou in memberships if ou.organization}

    tenant_slug = request.headers.get("X-Tenant-Slug")
    if tenant_slug:
        for org in por_id.values():
            if org.slug == tenant_slug:
                return org
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: No tienes permisos para acceder a esta organización.",
        )

    token_org = payload.get("org")
    if token_org and token_org in por_id:
        return por_id[token_org]
    return None


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

    if not token:
        raise credentials_exception

    # El TIPO se comprueba antes que nada: un refresh token bien firmado no es
    # una credencial de acceso. Antes solo se validaba la firma y se leía `sub`,
    # así que el refresh —que dura siete días frente a las 24 horas del access,
    # y viaja en una cookie que el navegador adjunta sola— servía como Bearer
    # contra la API entera.
    payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
    if not payload:
        raise credentials_exception

    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exception

    user = await load_user_with_organizations(db, user_id)
    if user is None:
        raise credentials_exception

    # Revocación en bloque (cambio/restablecimiento de contraseña). Vive en la
    # base, así que sigue aplicando aunque Redis esté caído.
    if token_predates_session_cutoff(payload, user):
        raise credentials_exception

    # Revocación de ESTE token (logout). La blocklist vive en Redis, y aquí la
    # política es fail-**open** a propósito: bloquear toda lectura porque el
    # cache no responde deja al cliente sin producto por un fallo de soporte.
    # Lo que sí se hace es marcar la sesión como no verificada, y las
    # superficies que autorizan gasto —aprobación y write-back— la rechazan
    # (ver `require_verified_session`). Leer el histórico ya sincronizado no
    # necesita esa garantía; escribir en Meta, sí.
    revocation_verified = True
    try:
        if await is_revoked(payload.get("jti")):
            raise credentials_exception
    except RevocationCheckUnavailable:
        revocation_verified = False
    user._revocation_verified = revocation_verified

    org = resolve_active_organization(request, user, payload)

    if org is not None:
        # Suspensión efectiva. Se comprueba en el único punto por el que pasan
        # todas las peticiones autenticadas: una compuerta por endpoint se
        # olvida en el siguiente endpoint que alguien añada.
        #
        # El administrador de plataforma la atraviesa porque necesita entrar
        # justamente a las organizaciones suspendidas —para exportar,
        # investigar o reactivarlas—; es la excepción que pide la historia 54.
        if org.status != "ACTIVE" and not getattr(user, "is_platform_admin", False):
            raise OrganizationSuspended(org.status)
        # Atributo no mapeado leído por User.organization_id (multi-tenant real).
        user._active_org_id = org.id
        user._active_org = org

    return user


async def require_verified_session(
    current_user: User = Depends(get_current_user),
) -> User:
    """Exige que la validez de la sesión se haya podido COMPROBAR.

    Se pone donde una acción compromete dinero o autoriza una escritura. Si el
    mecanismo de revocación no respondió, no se puede afirmar que esta sesión
    siga siendo válida, y aprobar o escribir bajo esa duda es exactamente lo que
    la revocación existe para impedir.

    Es la excepción acotada al fail-open general de la capa Redis (ADR-13): el
    resto —throttle, cache, debounce— sigue degradando abierto.
    """
    if not getattr(current_user, "_revocation_verified", True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SECURITY_CONTROLS_UNAVAILABLE",
                "message": (
                    "No se puede verificar la validez de la sesión en este momento, "
                    "así que esta acción queda bloqueada. La consulta de datos sigue "
                    "disponible. Reintenta en unos segundos."
                ),
            },
        )
    return current_user


async def revocation_backend_available() -> bool:
    """True si el mecanismo de revocación responde.

    Para los caminos que no tienen una sesión HTTP delante —los workers— y que
    aun así escriben en Meta.
    """
    client = aioredis.Redis.from_url(settings.REDIS_URL)
    try:
        await client.ping()
        return True
    except Exception as e:
        logger.warning("El mecanismo de revocación no responde: %s", e)
        return False
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def revoke_all_sessions(db: AsyncSession, user: User) -> None:
    """Invalida todas las credenciales vivas de `user`.

    Mueve el corte por usuario al instante actual. No enumera jti —no hay forma
    de conocerlos— y por eso alcanza también a los access tokens, que hasta
    ahora solo caducaban por expiración.
    """
    user.sessions_valid_from = session_cutoff_now()
    db.add(user)


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
            # `code` explicito: el frontend y el operador tienen que poder
            # distinguir "no contrataste esto" de "no tienes permiso". Son dos
            # problemas con dos soluciones distintas, y un mensaje suelto los
            # confunde (spec 05, criterio 6).
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ROLE_REQUIRED",
                    "message": f"Esta acción requiere el rol {min_role} o superior en la organización.",
                    "required_role": min_role,
                },
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


async def require_agency(
    current_user: User = Depends(get_current_user),
) -> User:
    """Restringe una superficie a la experiencia de agencia (spec 05).

    Se pone donde la respuesta expone la altura operativa: consumo y costo de
    IA, historial de llamadas a Meta, diagnósticos de infraestructura. No es que
    el cliente no deba "poder": es que esa información no significa nada para él
    y sí revela cómo está operado su servicio.
    """
    from app.services.experiencia import es_agencia

    if not es_agencia(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AGENCY_ONLY",
                "message": "Esta sección es parte de la operación de la agencia.",
            },
        )
    return current_user


def require_module(module: str):
    """Exige que la organización activa tenga habilitado `module`.

    Esta es la ÚNICA capa de las tres que es seguridad. Filtrar el menú y
    redirigir la ruta en el frontend son experiencia de usuario: sin esta
    dependencia el módulo está escondido, no apagado, y basta con llamar al
    endpoint a mano para seguir usándolo.

    **El módulo acota la experiencia del CLIENTE, no la de la agencia** (spec
    05). Antes acotaba a todos, así que apagar un módulo para simplificarle la
    vista al cliente se lo apagaba también al operador que necesita esa
    herramienta para prestar el servicio. La agencia se gobierna por rol.

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

        from app.services.experiencia import es_agencia

        if es_agencia(current_user, org_id):
            return current_user

        org = (await db.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one_or_none()
        if not org or not is_module_enabled(org, module):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "MODULE_DISABLED",
                    "message": f"El módulo '{AVAILABLE_MODULES[module]}' no está habilitado para tu organización.",
                    "module": module,
                },
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
