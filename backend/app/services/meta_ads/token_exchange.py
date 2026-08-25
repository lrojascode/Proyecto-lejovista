"""Intercambio de un user token de corta duración por uno long-lived (~60 días).

Los tokens generados a mano (Graph API Explorer / diálogo de permisos de la app)
duran horas — así se cayó el pipeline el 12-jul-2026 (cuentas EXPIRED en masa).
Con `grant_type=fb_exchange_token` el mismo token se canjea por uno de ~60 días
antes de persistirlo.

A propósito NO se usa `MetaAPIRateLimiter.call_api`: su auditoría (`APICallLog`)
solo enmascara la clave `access_token`, así que registraría `client_secret`,
`fb_exchange_token` y el token largo devuelto en texto plano. Esta llamada no
debe dejar rastro de secretos ni en logs ni en la tabla de auditoría.
"""
import datetime
import logging
from typing import Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Duración documentada de un long-lived user token cuando Meta no devuelve
# `expires_in` (a veces lo omite para tokens que ya eran long-lived).
_FALLBACK_TTL_DAYS = 60


def _app_credentials_configured(app_id: Optional[str] = None, app_secret: Optional[str] = None) -> bool:
    app_id = app_id or settings.META_APP_ID
    app_secret = app_secret or settings.META_APP_SECRET
    return bool(
        app_id
        and app_secret
        and app_id != "mock_app_id"
        and app_secret != "mock_app_secret"
    )


async def exchange_for_long_lived_token(
    short_lived_token: str,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
) -> Tuple[Optional[str], Optional[datetime.datetime]]:
    """Canjea `short_lived_token` por un long-lived token.

    `app_id`/`app_secret` son los de la app que EMITIÓ el token. Sin ellos se usa
    la app global de la agencia, que es el caso normal. Canjear con una app
    distinta de la que emitió el token no funciona: Meta responde error y el
    llamador se queda con el token original — que es lo correcto, pero silencioso.

    Devuelve `(token, expires_at)`. `(None, None)` si el intercambio no aplica
    (mock / app credentials sin configurar) o falla — el llamador decide qué
    hacer con el token original; conectar una cuenta nunca debe bloquearse por
    esto.
    """
    if settings.MOCK_SERVICES or not _app_credentials_configured(app_id, app_secret):
        return None, None

    url = f"https://graph.facebook.com/{settings.META_API_VERSION}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id or settings.META_APP_ID,
        "client_secret": app_secret or settings.META_APP_SECRET,
        "fb_exchange_token": short_lived_token,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(url, params=params)
            data = res.json()
    except Exception as exc:
        logger.warning("Intercambio a long-lived token falló (red/parseo): %s", exc)
        return None, None

    token = data.get("access_token")
    if not token:
        # No loguear `data` completo: ante respuestas inesperadas podría traer tokens.
        err = (data.get("error") or {}).get("message", "sin access_token en la respuesta")
        logger.warning("Meta rechazó el intercambio a long-lived token: %s", err)
        return None, None

    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(expires_in))
    else:
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=_FALLBACK_TTL_DAYS)

    logger.info("Token de Meta canjeado por long-lived (vence %s).", expires_at.date())
    return token, expires_at
