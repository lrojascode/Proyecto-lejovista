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
from app.services.notification_service import NotificationService
from app.services.email_service import send_email

logger = logging.getLogger(__name__)

# Umbral de aviso anticipado: 7 días
WARNING_THRESHOLD_DAYS = 7


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


async def inspect_token_expiration(
    user_token: str,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta el estado y fecha de expiración de un access token de Meta.

    En modo mock o tests:
    - Si el token contiene 'expired', devuelve expirado.
    - Si contiene 'expiring', devuelve expiración en 3 días.
    - Por defecto devuelve válido con 60 días de vigencia.

    En modo real:
    - Consulta `GET /debug_token` con App Access Token (`app_id|app_secret`).
    - Si no hay credenciales de app, hace fallback a `GET /me`.

    `app_id`/`app_secret` son los de la app que EMITIÓ el token; sin ellos se usa
    la app global. Preguntarle a Meta por un token con las credenciales de OTRA
    app devuelve error, y este error se lee más arriba como "token inválido":
    `check_expiring_tokens` marca la cuenta EXPIRED y suspende el sync sobre un
    token perfectamente sano. Por eso el par viaja hasta acá.
    """
    if not user_token:
        return {"is_valid": False, "expires_at": None, "scopes": [], "error": "Token vacío"}

    if settings.MOCK_SERVICES:
        now = datetime.datetime.utcnow()
        if "expired" in user_token.lower():
            return {
                "is_valid": False,
                "expires_at": now - datetime.timedelta(days=1),
                "scopes": ["ads_read"],
                "error": "Error 190: Error validating access token: Session has expired",
            }
        elif "expiring" in user_token.lower():
            return {
                "is_valid": True,
                "expires_at": now + datetime.timedelta(days=3),
                "scopes": ["ads_read", "ads_management"],
                "error": None,
            }
        elif "permanent" in user_token.lower():
            return {
                "is_valid": True,
                "expires_at": None,  # Nunca expira (System User)
                "scopes": ["ads_read", "ads_management"],
                "error": None,
            }
        return {
            "is_valid": True,
            "expires_at": now + datetime.timedelta(days=60),
            "scopes": ["ads_read", "ads_management"],
            "error": None,
        }

    # Modo Producción Real
    if _app_credentials_configured(app_id, app_secret):
        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/debug_token"
        app_access_token = (
            f"{app_id or settings.META_APP_ID}|{app_secret or settings.META_APP_SECRET}"
        )
        params = {
            "input_token": user_token,
            "access_token": app_access_token,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(url, params=params)
                data = res.json()
                if "error" in data:
                    return {
                        "is_valid": False,
                        "expires_at": None,
                        "scopes": [],
                        "error": data["error"].get("message", "Error de validación en Meta"),
                    }
                token_data = data.get("data", {})
                is_valid = bool(token_data.get("is_valid", False))
                expires_at_raw = token_data.get("expires_at", 0)
                expires_at = None
                if expires_at_raw and int(expires_at_raw) > 0:
                    expires_at = datetime.datetime.utcfromtimestamp(int(expires_at_raw))
                scopes = token_data.get("scopes", [])
                error_msg = token_data.get("error", {}).get("message") if not is_valid else None
                return {
                    "is_valid": is_valid,
                    "expires_at": expires_at,
                    "scopes": scopes,
                    "error": error_msg,
                }
        except Exception as exc:
            logger.warning(f"Error consultando debug_token en Meta: {exc}")
            return {"is_valid": False, "expires_at": None, "scopes": [], "error": str(exc)}

    # Fallback sin app credentials configuradas: /me
    url = f"https://graph.facebook.com/{settings.META_API_VERSION}/me"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url, params={"access_token": user_token, "fields": "id,name"})
            if res.status_code == 200:
                # No conocemos la fecha exacta, asumimos 60 días desde hoy
                return {
                    "is_valid": True,
                    "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=60),
                    "scopes": [],
                    "error": None,
                }
            return {
                "is_valid": False,
                "expires_at": None,
                "scopes": [],
                "error": res.text,
            }
    except Exception as exc:
        return {"is_valid": False, "expires_at": None, "scopes": [], "error": str(exc)}


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
        # credenciales de otra app devuelve error, y ese error se lee abajo como
        # "token inválido" → cuenta EXPIRED y sync suspendido sobre un token sano.
        cred_app_id, cred_app_secret = cred.app_credentials
        inspection = await inspect_token_expiration(raw_token, cred_app_id, cred_app_secret)
        is_valid = inspection["is_valid"]
        expires_at = inspection["expires_at"]

        # Calcular días restantes
        days_left = None
        if expires_at:
            delta = expires_at - now
            days_left = max(0, delta.days)

        if not is_valid:
            # Token expirado o revocado
            cred.status = "EXPIRED"
            acc.status = "EXPIRED"
            acc.last_sync_error_code = 190
            acc.last_sync_error_detail = inspection["error"] or "Token de acceso de Meta expirado o revocado."
            db.add(cred)
            db.add(acc)

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
            cred.token_expires_at = expires_at
            db.add(cred)

            # Notificar vía WebSocket al tenant
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
            # Válido y con tiempo suficiente o permanente
            if expires_at:
                cred.token_expires_at = expires_at
                db.add(cred)

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
