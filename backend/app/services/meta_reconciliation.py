"""Reconciliación con Meta antes de repetir una escritura (spec 03).

Cuando Meta responde con un timeout o un error ambiguo, el sistema no sabe si la
escritura ocurrió. Sin este paso, cualquier política de reintentos elige entre
duplicar o abandonar, y las dos son malas: duplicar gasta el doble del
presupuesto del cliente, abandonar deja una campaña huérfana que nadie sabe que
existe.

Reconciliar es **preguntarle a Meta por la huella de la intención**: buscar en la
cuenta la entidad que la operación habría creado, dentro de una ventana temporal
acotada.

**Ante ambigüedad no se adopta nada.** Si más de un candidato encaja, la
intención se queda en `VERIFYING` y hace falta una decisión humana. Es el riesgo
que más importa acá: adoptar como propia una entidad que creó otra persona en la
misma cuenta haría que la Saga colgara conjuntos y anuncios de una campaña ajena.
Preferir "no sé" a una adopción equivocada es lo que hace que este mecanismo sea
seguro de encender.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_proposal import MetaWriteIntent
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

logger = logging.getLogger("lejovista.reconciliacion")

# Cuánto hacia atrás se busca la entidad que la intención habría creado. Más
# ancho aumenta el riesgo de recoger algo ajeno; más estrecho pierde la escritura
# que sí ocurrió cuando el timeout fue largo. Una hora cubre con holgura una Saga
# entera (cinco pasos con 40 s de pausa entre ellos).
VENTANA_RECONCILIACION = datetime.timedelta(hours=1)

# Endpoint de Meta donde buscar lo que habría creado cada operación, y campos
# mínimos para poder decidir.
_BUSQUEDA = {
    "create_campaign": ("campaigns", "id,name,created_time,status"),
    "create_adset": ("adsets", "id,name,created_time,campaign_id"),
    "create_ad": ("ads", "id,name,created_time,adset_id"),
}


class ReconciliacionAmbigua(Exception):
    """Más de un candidato encaja con la huella. No se adopta ninguno."""

    def __init__(self, candidatos: List[str]):
        self.candidatos = candidatos
        super().__init__(
            f"La reconciliación encontró {len(candidatos)} entidades que encajan con "
            f"la misma intención ({', '.join(candidatos)}). No se adopta ninguna: "
            f"hace falta revisarlo a mano."
        )


def _creada_dentro_de_la_ventana(entidad: Dict[str, Any], desde: datetime.datetime) -> bool:
    crudo = entidad.get("created_time")
    if not crudo:
        # Sin fecha no se puede acotar, y una entidad vieja con el mismo nombre
        # es exactamente el falso positivo que hay que evitar.
        return False
    try:
        # Meta devuelve ISO-8601 con offset (ej. 2026-08-31T22:10:05-0700).
        limpio = str(crudo).replace("+0000", "+00:00")
        if len(limpio) >= 5 and limpio[-5] in "+-" and ":" not in limpio[-5:]:
            limpio = limpio[:-2] + ":" + limpio[-2:]
        creada = datetime.datetime.fromisoformat(limpio)
    except ValueError:
        return False
    if creada.tzinfo is not None:
        creada = creada.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return creada >= desde


async def buscar_entidad_de_la_intencion(
    db: AsyncSession,
    intencion: MetaWriteIntent,
    *,
    ahora: Optional[datetime.datetime] = None,
) -> Optional[str]:
    """El id de Meta de lo que esta intención habría creado, o `None`.

    `None` significa "no existe": el reintento es seguro. Levantar
    `ReconciliacionAmbigua` significa "no se puede decidir": el reintento NO es
    seguro y hace falta una persona.
    """
    busqueda = _BUSQUEDA.get(intencion.operation)
    if busqueda is None or not intencion.correlation_tag:
        # Un creativo no tiene nombre consultable de la misma forma; para esas
        # operaciones no hay reconciliación automática y la intención se queda
        # en VERIFYING, que es lo honesto.
        return None

    borde, campos = busqueda
    acc = await db.get(AdAccount, intencion.ad_account_id)
    if acc is None:
        return None

    limiter = MetaAPIRateLimiter(
        ad_account_id=acc.id, external_account_id=acc.external_account_id
    )
    respuesta = await limiter.call_api(
        f"{acc.external_account_id}/{borde}",
        {"fields": campos, "limit": 200},
        access_token=acc.get_access_token(),
    )
    if not isinstance(respuesta, dict) or "error" in respuesta:
        logger.warning(
            "Reconciliación de %s: Meta no respondió con datos usables (%s).",
            intencion.idempotency_key, respuesta,
        )
        return None

    desde = (ahora or datetime.datetime.utcnow()) - VENTANA_RECONCILIACION
    candidatos = [
        str(e.get("id"))
        for e in (respuesta.get("data") or [])
        if e.get("name") == intencion.correlation_tag
        and _creada_dentro_de_la_ventana(e, desde)
    ]

    if not candidatos:
        return None
    if len(candidatos) > 1:
        raise ReconciliacionAmbigua(candidatos)
    return candidatos[0]
