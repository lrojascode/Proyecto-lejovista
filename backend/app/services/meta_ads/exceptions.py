"""Excepciones tipadas para clasificar fallos de la API de Meta.

Permiten a las tareas de Celery decidir si reintentar (transitorio) o
marcar el trabajo como fallido definitivo (permanente).
"""


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
