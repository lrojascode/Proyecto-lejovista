"""Excepciones tipadas para clasificar fallos de la API de Meta.

Permiten a las tareas de Celery decidir si reintentar (transitorio) o
marcar el trabajo como fallido definitivo (permanente).
"""


from typing import Any, Optional


class MetaSyncError(Exception):
    """Base para errores de sincronización/escritura contra Meta."""


class TransientMetaError(MetaSyncError):
    """Fallo temporal (429, 5xx, timeout, red). Se debe reintentar con backoff."""


class PermanentMetaError(MetaSyncError):
    """Fallo permanente (400, 403, token inválido, config errónea). No reintentar."""


# Marcadores que sugieren que un error es transitorio (reintentable).
_TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504",
    "rate limit", "rate-limit", "too many",
    "timeout", "timed out", "temporarily",
    "failed_after_retries", "network", "connection",
)


def classify_meta_error(exc: Exception) -> MetaSyncError:
    """Clasifica una excepción genérica como transitoria o permanente por su mensaje."""
    msg = str(exc).lower()
    if any(marker in msg for marker in _TRANSIENT_MARKERS):
        return TransientMetaError(str(exc))
    return PermanentMetaError(str(exc))


def extract_meta_error_diagnostic(error_or_response: Any) -> tuple[Optional[int], str]:
    """Extrae código numérico y descripción accionable de fallos de Meta API (3.3).

    - Code 190: Token expirado o revocado
    - Code 17 / 80004: Rate limit
    - Code 200 / 10 / 100: Permisos insuficientes (ads_read / ads_management)
    """
    code: Optional[int] = None
    raw_msg = ""

    if isinstance(error_or_response, dict):
        err = error_or_response.get("error") or error_or_response.get("ERROR") or {}
        if isinstance(err, dict):
            code = err.get("code")
            raw_msg = str(err.get("message") or "")
        else:
            raw_msg = str(err)
    elif isinstance(error_or_response, Exception):
        raw_msg = str(error_or_response)

    lower = raw_msg.lower()

    if code == 190 or "190" in raw_msg or "oauthexception" in lower or "session has expired" in lower or "error validating access token" in lower:
        return 190, "Token de Meta expirado o revocado. Reconecta tu cuenta en Configuración."

    if code in (17, 80004) or "rate limit" in lower or "user request limit reached" in lower or "too many" in lower:
        return code or 17, "Meta está limitando las peticiones (rate limit). Reintentando automáticamente con backoff."

    if code in (200, 10, 100) or "permission" in lower or "ads_read" in lower or "ads_management" in lower:
        return code or 200, "Falta el permiso ads_read o ads_management. Revisa los permisos de tu app en Meta Developers."

    return code, raw_msg or "Error no especificado al comunicar con Meta Ads."

