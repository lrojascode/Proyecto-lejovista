"""Inspección de tokens de acceso de Meta Ads y aviso anticipado de expiración (Fase 6.4 / Roadmap 7)."""
import datetime
import logging
from typing import Optional, Dict, Any, List
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.ad_account import AdAccount
from app.models.meta_credential import MetaCredential
from app.models.user import User
from app.models.organization_user import OrganizationUser
from app.services.meta_ads.exceptions import extract_meta_error_diagnostic
from app.services.notification_service import NotificationService
from app.services.email_service import send_email

logger = logging.getLogger(__name__)

# Umbral de aviso anticipado: 7 días
WARNING_THRESHOLD_DAYS = 7

# Tres estados, no dos. "No pude preguntar" no es "está muerto": esa confusión es
# la que marcaba EXPIRED una cuenta sana todas las mañanas a las 08:00.
ESTADO_VALIDO = "VALID"
ESTADO_INVALIDO = "INVALID"
ESTADO_INDETERMINADO = "UNDETERMINED"


def _resultado(
    estado: str,
    expires_at: Optional[datetime.datetime] = None,
    scopes: Optional[List[str]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """`is_valid` se conserva para los llamadores viejos, pero solo es True
    cuando la inspección fue concluyente."""
    return {
        "estado": estado,
        "is_valid": estado == ESTADO_VALIDO,
        "expires_at": expires_at,
        "scopes": scopes or [],
        "error": error,
    }


def _app_credentials_configured(app_id: Optional[str] = None, app_secret: Optional[str] = None) -> bool:
    """¿Hay un par app_id/secret utilizable? El propio de la credencial si lo
    tiene, si no el global de la agencia."""
    app_id = app_id or settings.META_APP_ID
    app_secret = app_secret or settings.META_APP_SECRET
    return bool(
        app_id
        and app_secret
        and app_id != "mock_app_id"
        and app_secret != "mock_app_secret"
    )


def _inspeccion_mock(user_token: str) -> Dict[str, Any]:
    now = datetime.datetime.utcnow()
    if "expired" in user_token.lower():
        return _resultado(
            ESTADO_INVALIDO,
            expires_at=now - datetime.timedelta(days=1),
            scopes=["ads_read"],
            error="Error 190: Error validating access token: Session has expired",
        )
    if "expiring" in user_token.lower():
        return _resultado(
            ESTADO_VALIDO,
            expires_at=now + datetime.timedelta(days=3),
            scopes=["ads_read", "ads_management"],
        )
    if "permanent" in user_token.lower():
        # Nunca expira (System User).
        return _resultado(ESTADO_VALIDO, expires_at=None, scopes=["ads_read", "ads_management"])
    return _resultado(
        ESTADO_VALIDO,
        expires_at=now + datetime.timedelta(days=60),
        scopes=["ads_read", "ads_management"],
    )


async def _debug_token(
    user_token: str, app_id: Optional[str], app_secret: Optional[str]
) -> Dict[str, Any]:
    """`GET /debug_token` con un App Access Token (`app_id|app_secret`)."""
    url = f"https://graph.facebook.com/{settings.META_API_VERSION}/debug_token"
    app_access_token = (
        f"{app_id or settings.META_APP_ID}|{app_secret or settings.META_APP_SECRET}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                url,
                params={"input_token": user_token, "access_token": app_access_token},
            )
            data = res.json()
    except Exception as exc:
        logger.warning(f"Error consultando debug_token en Meta: {exc}")
        return _resultado(ESTADO_INDETERMINADO, error=str(exc))

    # Un error de PRIMER nivel es de la CONSULTA, no del token: la app con la que
    # preguntamos no tiene derecho a depurar este token. Es lo que pasa con un
    # System User que vive en el Business Manager del cliente y solo puede emitir
    # tokens con la app de ese negocio. Lo que Meta opina del token viene siempre
    # dentro de `data`; leer el error de arriba como "token inválido" es dar por
    # muerto un token que funciona perfecto.
    if "error" in data:
        return _resultado(
            ESTADO_INDETERMINADO,
            error=data["error"].get("message", "Error de validación en Meta"),
        )

    token_data = data.get("data") or {}
    if not token_data:
        return _resultado(ESTADO_INDETERMINADO, error="Respuesta de debug_token sin datos")

    expires_at = None
    expires_at_raw = token_data.get("expires_at", 0)
    if expires_at_raw and int(expires_at_raw) > 0:
        expires_at = datetime.datetime.utcfromtimestamp(int(expires_at_raw))
    scopes = token_data.get("scopes", [])

    if not token_data.get("is_valid", False):
        return _resultado(
            ESTADO_INVALIDO,
            expires_at=expires_at,
            scopes=scopes,
            error=(token_data.get("error") or {}).get("message"),
        )
    return _resultado(ESTADO_VALIDO, expires_at=expires_at, scopes=scopes)


async def _probar_contra_meta(
    user_token: str, external_account_id: Optional[str] = None
) -> Dict[str, Any]:
    """¿Este token sirve para leer lo que tiene que leer?

    Es el desempate cuando `debug_token` no se puede responder. No dice cuándo
    vence —eso solo lo sabe `debug_token` con la app correcta— pero sí lo único
    que importa operativamente: si el sync de mañana va a funcionar. Se pregunta
    por la cuenta publicitaria cuando se sabe cuál es, porque es exactamente el
    permiso que el sync necesita; `/me` solo prueba que el token existe.
    """
    endpoint = external_account_id or "me"
    url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url, params={"access_token": user_token, "fields": "id"})
            data = res.json()
    except Exception as exc:
        logger.warning(f"No se pudo sondear el token contra Meta: {exc}")
        return _resultado(ESTADO_INDETERMINADO, error=str(exc))

    if res.status_code == 200 and isinstance(data, dict) and "error" not in data:
        # Sirve, pero la fecha de caducidad queda desconocida: `None` acá
        # significa "sin caducidad conocida", nunca los 60 días inventados de antes.
        return _resultado(ESTADO_VALIDO)

    code, detalle = extract_meta_error_diagnostic(data)
    if code == 190:
        return _resultado(ESTADO_INVALIDO, error=detalle)
    # Un permiso que falta o un rate limit no dicen nada sobre la vigencia del token.
    return _resultado(ESTADO_INDETERMINADO, error=detalle)


async def inspect_token_expiration(
    user_token: str,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
    external_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta el estado y fecha de expiración de un access token de Meta.

    Devuelve `estado` (`VALID` / `INVALID` / `UNDETERMINED`), `expires_at`
    (`None` = sin caducidad conocida), `scopes` y `error`.

    En modo mock o tests:
    - Si el token contiene 'expired', devuelve expirado.
    - Si contiene 'expiring', devuelve expiración en 3 días.
    - Si contiene 'permanent', devuelve válido sin caducidad.
    - Por defecto devuelve válido con 60 días de vigencia.

    En modo real se pregunta con `debug_token` usando la app que EMITIÓ el token
    (`app_id`/`app_secret`; sin ellos, la app global de la agencia). Cuando esa
    consulta no se puede responder —la app no es la que emitió el token, o se
    cayó la red— NO se concluye nada: se sondea el token contra la cuenta y se
    deja que el resultado de ese sondeo decida.
    """
    if not user_token:
        return _resultado(ESTADO_INVALIDO, error="Token vacío")

    if settings.MOCK_SERVICES:
        return _inspeccion_mock(user_token)

    if _app_credentials_configured(app_id, app_secret):
        inspeccion = await _debug_token(user_token, app_id, app_secret)
        if inspeccion["estado"] != ESTADO_INDETERMINADO:
            return inspeccion
        logger.warning(
            "debug_token no fue concluyente (%s); se sondea el token contra Meta.",
            inspeccion["error"],
        )

    return await _probar_contra_meta(user_token, external_account_id)


def aplicar_inspeccion(
    acc: AdAccount, cred: MetaCredential, inspeccion: Dict[str, Any]
) -> str:
    """Vuelca el resultado de una inspección sobre la cuenta y su credencial.

    Lo comparten la revisión diaria y el botón "Verificar Token": tenían la misma
    lógica escrita dos veces y solo una de las dos sabía devolver una cuenta a
    ACTIVE, así que una cuenta marcada por error se quedaba con el banner rojo
    hasta que alguien reconectaba a mano.
    """
    estado = inspeccion.get("estado") or (
        ESTADO_VALIDO if inspeccion.get("is_valid") else ESTADO_INVALIDO
    )

    if estado == ESTADO_INDETERMINADO:
        # No se pudo saber: se deja la cuenta como está. Marcarla EXPIRED por una
        # consulta que no se pudo hacer es justamente el bug que se arregla acá.
        return estado

    if estado == ESTADO_INVALIDO:
        cred.status = "EXPIRED"
        acc.status = "EXPIRED"
        acc.last_sync_error_code = 190
        acc.last_sync_error_detail = (
            inspeccion.get("error") or "Token de acceso de Meta expirado o revocado."
        )
        return estado

    # Válido. `expires_at` se escribe SIEMPRE, incluso cuando es None: a un token
    # permanente (System User, `expires_at: 0` en Meta) se le había guardado una
    # caducidad inventada de 60 días que nadie borraba, y a los ~53 días arrancaba
    # el aviso de "vence en 7 días" sobre un token que no vence nunca.
    cred.token_expires_at = inspeccion.get("expires_at")
    cred.status = "ACTIVE"
    if acc.status == "EXPIRED":
        acc.status = "ACTIVE"
    if acc.last_sync_error_code == 190:
        acc.last_sync_error_code = None
        acc.last_sync_error_detail = None
    return estado


async def check_expiring_tokens(db: AsyncSession) -> List[Dict[str, Any]]:
    """Inspecciona todas las cuentas activas y notifica si el token vence en < 7 días o ya expiró."""
    stmt = (
        select(AdAccount)
        .options(selectinload(AdAccount.meta_credential), selectinload(AdAccount.organization))
        .where(AdAccount.status != "ARCHIVED")
    )
    result = await db.execute(stmt)
    accounts = result.scalars().all()

    notifier = NotificationService()
    inspected_results = []
    now = datetime.datetime.utcnow()

    for acc in accounts:
        cred = acc.meta_credential
        if not cred:
            continue

        raw_token = cred.get_access_token()
        if not raw_token:
            continue

        # La app que emitió ESTE token, no la global: preguntarle a Meta con las
        # credenciales de otra app devuelve un error de la consulta, no del token.
        cred_app_id, cred_app_secret = cred.app_credentials
        inspection = await inspect_token_expiration(
            raw_token, cred_app_id, cred_app_secret, acc.external_account_id
        )
        estado = aplicar_inspeccion(acc, cred, inspection)
        db.add(cred)
        db.add(acc)

        if estado == ESTADO_INDETERMINADO:
            logger.warning(
                "No se pudo determinar el estado del token de la cuenta %s (%s): %s. "
                "Se deja el estado como estaba.",
                acc.name, acc.id, inspection.get("error"),
            )
            inspected_results.append({
                "account_id": acc.id,
                "name": acc.name,
                "status": "UNDETERMINED",
                "days_left": None,
                "warning": False,
            })
            continue

        expires_at = inspection["expires_at"]
        days_left = None
        if expires_at:
            days_left = max(0, (expires_at - now).days)

        if estado == ESTADO_INVALIDO:
            # Notificar vía WebSocket al tenant
            notifier.notify_job_status(
                tenant_id=acc.organization_id,
                job_id=acc.id,
                status="EXPIRED",
                event_type="token_expired",
                extra={
                    "account_name": acc.name,
                    "external_account_id": acc.external_account_id,
                    "error_detail": acc.last_sync_error_detail,
                },
            )

            # Enviar email de alerta a administradores
            await _notify_admins_by_email(
                db=db,
                organization_id=acc.organization_id,
                subject=f"⚠️ Token de Meta expirado para la cuenta {acc.name}",
                body=(
                    f"El token de acceso de Meta Ads para la cuenta '{acc.name}' ({acc.external_account_id}) "
                    f"ha expirado o ha sido revocado. La sincronización y optimización automática están suspendidas "
                    f"hasta que reconectes el token en el panel de Configuración de LejoVista."
                ),
            )

            inspected_results.append({
                "account_id": acc.id,
                "name": acc.name,
                "status": "EXPIRED",
                "days_left": 0,
                "warning": True,
            })

        elif days_left is not None and days_left <= WARNING_THRESHOLD_DAYS:
            # Próximo a vencer (< 7 días)
            notifier.notify_job_status(
                tenant_id=acc.organization_id,
                job_id=acc.id,
                status="EXPIRING_SOON",
                event_type="token_expiring_warning",
                extra={
                    "account_name": acc.name,
                    "external_account_id": acc.external_account_id,
                    "days_left": days_left,
                    "expires_at": expires_at.isoformat(),
                },
            )

            # Enviar email preventivo a administradores
            await _notify_admins_by_email(
                db=db,
                organization_id=acc.organization_id,
                subject=f"⏰ Tu token de Meta para '{acc.name}' vence en {days_left} días",
                body=(
                    f"El token de acceso de Meta Ads para la cuenta publicitaria '{acc.name}' "
                    f"({acc.external_account_id}) vencerá el {expires_at.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"(quedan {days_left} días).\n\n"
                    f"Por favor ingresa a Configuración en LejoVista y reconéctalo para evitar interrupciones en tus campañas."
                ),
            )

            inspected_results.append({
                "account_id": acc.id,
                "name": acc.name,
                "status": "EXPIRING_SOON",
                "days_left": days_left,
                "warning": True,
            })
        else:
            # Válido y con tiempo suficiente, o permanente.
            inspected_results.append({
                "account_id": acc.id,
                "name": acc.name,
                "status": "OK",
                "days_left": days_left,
                "warning": False,
            })

    await db.commit()
    return inspected_results


async def _notify_admins_by_email(db: AsyncSession, organization_id: str, subject: str, body: str):
    """Envía un email a los administradores de la organización (best-effort)."""
    try:
        stmt = (
            select(User)
            .join(OrganizationUser, OrganizationUser.user_id == User.id)
            .where(
                OrganizationUser.organization_id == organization_id,
                OrganizationUser.role == "ADMIN",
            )
        )
        res = await db.execute(stmt)
        admins = res.scalars().all()
        for admin in admins:
            send_email(
                to_email=admin.email,
                to_name=admin.full_name,
                subject=subject,
                text_content=body,
            )
    except Exception as exc:
        logger.warning(f"No se pudo enviar email de alerta de token a los admins: {exc}")
