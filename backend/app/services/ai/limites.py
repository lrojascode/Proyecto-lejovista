"""Límites de frecuencia y de reintentos de las llamadas al modelo (spec 10, 16).

Un bucle de reintentos es la forma más barata de consumir el presupuesto de un
cliente en minutos, y es la que más fácil se cuela: no hace falta un atacante,
alcanza con un error transitorio del proveedor y un `while` optimista.

Dos topes independientes, porque atajan cosas distintas:

* **Reintentos** — cuántas veces se repite UNA llamada que falló. Con retroceso,
  para no castigar al proveedor que ya está en problemas.
* **Frecuencia** — cuántas llamadas por minuto puede hacer una Organización
  entera. Es el que ataja el bucle que no reintenta sino que vuelve a empezar,
  contra el que un tope de reintentos no puede nada.

**Política ante caída de Redis: fail-open, con red local.** Es un throttle, y
ADR-13 quedó derogada solo en revocación, aprobación y write-back (spec 01); un
limitador de frecuencia no autoriza gasto irreversible ni concede acceso. Pero
fail-open a secas dejaría el bucle completamente suelto justo cuando la
infraestructura está en problemas, así que además se cuenta **en memoria del
proceso**. Ese contador no ve a los otros trabajadores —el techo agregado pasa a
ser el límite por trabajador— pero acota el caso que importa, que es un solo
proceso girando en falso.

El contador local se consulta SIEMPRE, no solo cuando Redis falla. Solo puede
contar de menos respecto del global, así que nunca bloquea algo que Redis habría
dejado pasar: es estrictamente una red por debajo, nunca una restricción extra.
"""
import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger("lejovista.ai.limites")

VENTANA_SEGUNDOS = 60

# Retroceso entre reintentos, en segundos. Fijo y corto: son llamadas dentro de
# una petición del usuario, y una espera exponencial larga se traduce en una
# conversación que parece colgada.
RETROCESO = (0.5, 1.5, 3.0)

_local: Dict[str, Deque[float]] = defaultdict(deque)


class LimiteDeFrecuencia(Exception):
    """La Organización superó su cupo de llamadas al modelo por minuto."""

    code = "AI_RATE_LIMITED"

    def __init__(self, *, org_id: str, limite: int, alcance: str = "global"):
        self.org_id = org_id
        self.limite = limite
        self.alcance = alcance
        super().__init__(
            f"La organización {org_id} superó el límite de {limite} llamadas al "
            f"modelo por minuto (contador {alcance})."
        )

    def as_detail(self) -> dict:
        return {
            "code": self.code,
            "message": (
                "Se están pidiendo respuestas de inteligencia artificial más "
                "rápido de lo que la cuenta tiene permitido. Esperá un momento y "
                "volvé a intentar."
            ),
            "limite_por_minuto": self.limite,
        }


def max_reintentos() -> int:
    """Reintentos ADICIONALES por llamada. 0 significa un solo intento."""
    return max(0, int(getattr(settings, "AI_MAX_REINTENTOS", 2)))


def espera_de_reintento(intento: int) -> float:
    """Segundos a esperar antes del reintento número `intento` (1-based)."""
    if intento <= 0:
        return 0.0
    return RETROCESO[min(intento, len(RETROCESO)) - 1]


def limite_por_minuto() -> int:
    """Llamadas al modelo por minuto y por Organización. 0 = sin límite."""
    return max(0, int(getattr(settings, "AI_MAX_LLAMADAS_POR_MINUTO", 30)))


def _contar_local(org_id: str, limite: int, ahora: Optional[float] = None) -> int:
    """Registra la llamada en la ventana en memoria y devuelve cuántas van."""
    t = time.monotonic() if ahora is None else ahora
    cola = _local[org_id]
    corte = t - VENTANA_SEGUNDOS
    while cola and cola[0] < corte:
        cola.popleft()
    cola.append(t)
    return len(cola)


def reset_contador_local(org_id: Optional[str] = None) -> None:
    """Limpia la ventana en memoria. Para las pruebas y para el arranque."""
    if org_id is None:
        _local.clear()
    else:
        _local.pop(org_id, None)


async def assert_frecuencia(org_id: str) -> None:
    """Levanta `LimiteDeFrecuencia` si la Organización se pasó del cupo.

    Se llama una vez por llamada real al modelo, no por petición del usuario: una
    petición puede disparar varias llamadas —clasificar, consultar, redactar— y
    lo que se quiere acotar es el consumo, no la navegación.
    """
    limite = limite_por_minuto()
    if not limite or not org_id:
        return

    # Red local primero: no depende de nada externo y es la que sigue en pie
    # cuando Redis no está.
    if _contar_local(org_id, limite) > limite:
        raise LimiteDeFrecuencia(org_id=org_id, limite=limite, alcance="proceso")

    clave = f"ai:rate:{org_id}"
    client = redis.Redis.from_url(settings.REDIS_URL)
    try:
        cuenta = await client.incr(clave)
        if cuenta == 1:
            await client.expire(clave, VENTANA_SEGUNDOS)
        if cuenta > limite:
            raise LimiteDeFrecuencia(org_id=org_id, limite=limite)
    except LimiteDeFrecuencia:
        raise
    except Exception as e:
        logger.warning(
            "El limitador de frecuencia de IA no está disponible (%s); sigue "
            "vigente el contador en memoria del proceso.", e,
        )
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def esperar_antes_de_reintentar(intento: int) -> None:
    espera = espera_de_reintento(intento)
    if espera > 0:
        await asyncio.sleep(espera)
