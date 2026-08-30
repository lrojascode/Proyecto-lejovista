"""Excepciones tipadas para clasificar fallos de la API de Meta.

Permiten a las tareas de Celery decidir si reintentar (transitorio) o
marcar el trabajo como fallido definitivo (permanente).

La clasificación va **por código**, no por el texto del mensaje. Meta manda
`type: OAuthException` en muchísimo más que la expiración de un token —rate
limits (4, 17, 32, 800xx), permisos (200, 10), objetos inexistentes (100)—, así
que buscar "oauthexception" en el mensaje daba por expirado cualquier fallo. Con
`acc.status = "EXPIRED"` colgando de ese diagnóstico, un rate limit de la tarde
dejaba al cliente con el banner rojo de "reconecta tu cuenta" sobre un token
sano. Lo mismo con `"190" in mensaje`: un `fbtrace_id` o un id de campaña con
esos tres caracteres alcanzaba.
"""


import re
from typing import Any, Optional


class MetaSyncError(Exception):
    """Base para errores de sincronización/escritura contra Meta."""


class TransientMetaError(MetaSyncError):
    """Fallo temporal (429, 5xx, timeout, red). Se debe reintentar con backoff."""


class PermanentMetaError(MetaSyncError):
    """Fallo permanente (400, 403, token inválido, config errónea). No reintentar."""


class MetaApiError(MetaSyncError):
    """Un error devuelto por la Graph API, con el cuerpo crudo intacto.

    `call_api` devuelve el JSON de Meta tal cual y el sync lo interpolaba en un
    `Exception` genérico. Ahí se perdía lo único que distingue un token muerto de
    un rate limit —el `code`— y clasificarlo obligaba a adivinar por substring.
    Guardando el diccionario, `extract_meta_error_diagnostic` lee el código exacto.
    """

    def __init__(self, mensaje: str, payload: Any = None):
        self.payload = payload
        super().__init__(f"{mensaje}: {payload}" if payload is not None else mensaje)


# Familias de códigos de Meta. El texto de los mensajes cambia entre versiones de
# la API y entre idiomas; el código no.
#
# Los subcódigos de un token muerto (458 app desinstalada, 460 cambió la
# contraseña, 463 expiró, 467 inválido...) viajan todos bajo el code 190.
CODIGOS_TOKEN_INVALIDO = frozenset({102, 190})
CODIGOS_RATE_LIMIT = frozenset({4, 17, 32, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80014})
SUBCODIGOS_RATE_LIMIT = frozenset({1349174, 2446079})
CODIGOS_PERMISOS = frozenset({10, 200, 272, 299})

# Solo frases que hablan del token en sí. Sirven para los errores que llegan sin
# código (excepciones nuestras, mensajes sueltos); nunca para ganarle a un código.
_TEXTO_TOKEN_INVALIDO = (
    "error validating access token",
    "session has expired",
    "access token has expired",
    "invalid oauth access token",
    "the access token could not be decrypted",
    "session is invalid",
)

_TEXTO_PERMISOS = ("permission", "ads_read", "ads_management")

# Marcadores que sugieren que un error es transitorio (reintentable). Los
# numéricos van con borde de palabra: sin eso, un `fbtrace_id` o un importe con
# "500" adentro convertía un fallo permanente en un reintento infinito.
_TRANSIENT_MARKERS = (
    "rate limit", "rate-limit", "too many",
    "timeout", "timed out", "temporarily",
    "failed_after_retries", "network", "connection",
)
_RE_TRANSIENT_STATUS = re.compile(r"\b(429|500|502|503|504)\b")

# El payload de Meta llega a veces como `repr` de un dict dentro del mensaje de
# una excepción. `error_subcode` no matchea por el `_` que precede a "code".
_RE_CODE = re.compile(r"['\"]code['\"]\s*:\s*(\d+)")
_RE_SUBCODE = re.compile(r"['\"]error_subcode['\"]\s*:\s*(\d+)")


def _entero(valor: Any) -> Optional[int]:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _desempaquetar(error_or_response: Any) -> tuple[Optional[int], Optional[int], str]:
    """`(code, error_subcode, mensaje)` de un dict de Meta, un `MetaApiError` o
    una excepción cualquiera."""
    fuente = error_or_response
    if isinstance(fuente, MetaApiError) and fuente.payload is not None:
        fuente = fuente.payload

    if isinstance(fuente, dict):
        err = fuente.get("error") or fuente.get("ERROR") or {}
        if isinstance(err, dict):
            return (
                _entero(err.get("code")),
                _entero(err.get("error_subcode")),
                str(err.get("message") or ""),
            )
        detalle = fuente.get("detalle") or ""
        return None, None, f"{err} {detalle}".strip()

    texto = str(fuente)
    code = _RE_CODE.search(texto)
    subcode = _RE_SUBCODE.search(texto)
    return (
        int(code.group(1)) if code else None,
        int(subcode.group(1)) if subcode else None,
        texto,
    )


def _es_rate_limit(code: Optional[int], subcode: Optional[int]) -> bool:
    return code in CODIGOS_RATE_LIMIT or subcode in SUBCODIGOS_RATE_LIMIT


def classify_meta_error(exc: Exception) -> MetaSyncError:
    """Clasifica una excepción como transitoria o permanente.

    El código manda sobre el texto: `str(exc)` de un payload de Meta trae ids,
    importes y `fbtrace_id`, y buscar marcadores ahí adentro es una lotería.
    """
    code, subcode, _ = _desempaquetar(exc)

    if _es_rate_limit(code, subcode):
        return TransientMetaError(str(exc))

    if code in CODIGOS_TOKEN_INVALIDO or code in CODIGOS_PERMISOS:
        return PermanentMetaError(str(exc))

    msg = str(exc).lower()
    if any(marker in msg for marker in _TRANSIENT_MARKERS) or _RE_TRANSIENT_STATUS.search(msg):
        return TransientMetaError(str(exc))
    return PermanentMetaError(str(exc))


def extract_meta_error_diagnostic(error_or_response: Any) -> tuple[Optional[int], str]:
    """Extrae código numérico y descripción accionable de fallos de Meta API (3.3).

    - Code 4 / 17 / 32 / 613 / 800xx: rate limit
    - Code 190 (y 102): token expirado o revocado
    - Code 10 / 200 / 272 / 299: permisos insuficientes (ads_read / ads_management)

    El rate limit se evalúa **primero** a propósito: comparte el
    `type: OAuthException` con la expiración del token, y confundirlos marca la
    cuenta EXPIRED cada vez que Meta la está limitando.
    """
    code, subcode, raw_msg = _desempaquetar(error_or_response)
    lower = raw_msg.lower()

    if _es_rate_limit(code, subcode):
        return code or 17, "Meta está limitando las peticiones (rate limit). Reintentando automáticamente con backoff."

    if code in CODIGOS_TOKEN_INVALIDO or any(frase in lower for frase in _TEXTO_TOKEN_INVALIDO):
        return 190, "Token de Meta expirado o revocado. Reconecta tu cuenta en Configuración."

    if code in CODIGOS_PERMISOS or any(frase in lower for frase in _TEXTO_PERMISOS):
        return code or 200, "Falta el permiso ads_read o ads_management. Revisa los permisos de tu app en Meta Developers."

    return code, raw_msg or "Error no especificado al comunicar con Meta Ads."
