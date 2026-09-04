"""Dos audiencias, dos mensajes (spec 13, criterios 13 y 14).

Durante un incidente, la agencia necesita saber **qué dependencia falla, qué
interruptor está activo y qué devolvió Meta**; el cliente necesita saber **qué
puede y qué no puede hacer ahora**. Son dos preguntas distintas y la respuesta a
la primera no le sirve a nadie que no pueda actuar sobre ella.

Darle el diagnóstico técnico al cliente es peor que inútil: le enseña nombres de
infraestructura que no puede usar, y lo alarma sobre algo que no está en su mano
arreglar. Darle a la agencia el mensaje del cliente la deja sin lo único que
necesita para arreglarlo.

**La lectura del histórico se conserva siempre** (criterio 14). Un incidente que
afecta a la escritura no puede quitarle al cliente la consulta de lo que ya
ocurrió: es justo cuando más quiere mirar qué está pasando con su dinero.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import interruptores, senales

logger = logging.getLogger("lejovista.incidente")

# Lo que sigue disponible pase lo que pase. Se enumera aquí y no se deduce del
# estado de los interruptores porque es una promesa del producto, no una
# consecuencia: quien lea esta lista tiene que poder confiar en ella sin
# reconstruir qué está apagado.
SIEMPRE_DISPONIBLE = (
    "Consultar tus campañas y sus resultados",
    "Ver tu historial completo",
    "Descargar tus informes",
)


async def diagnostico_para_la_agencia(
    db: AsyncSession, *, organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Qué falla, qué está apagado y desde cuándo.

    Junta las señales funcionales con el estado de los interruptores: durante un
    incidente la primera pregunta es "¿esto se cayó o lo apagamos nosotros?", y
    responderla exige mirar las dos cosas a la vez.
    """
    from app.models.operacion import OperationalChange

    estado_senales = await senales.resumen(db)
    estado_switches = await interruptores.panorama(db, organization_id=organization_id)

    try:
        cambios = (await db.execute(
            select(OperationalChange)
            .order_by(OperationalChange.created_at.desc())
            .limit(10)
        )).scalars().all()
    except Exception:
        logger.exception("No se pudo leer la bitácora de cambios operativos.")
        cambios = []

    return {
        "estado": estado_senales["estado"],
        "alertas": estado_senales["alertas"],
        "senales": estado_senales["senales"],
        "interruptores": estado_switches,
        # Quién movió qué, para no tener que preguntárselo a alguien que está
        # ocupado apagando el fuego.
        "cambios_recientes": [
            {
                "tipo": c.tipo,
                "clave": c.clave,
                "organization_id": c.organization_id,
                "de": c.valor_anterior,
                "a": c.valor_nuevo,
                "motivo": c.motivo,
                "actor": c.actor_email,
                "cuando": c.created_at.isoformat() if c.created_at else None,
            }
            for c in cambios
        ],
    }


async def aviso_para_el_cliente(
    db: AsyncSession, *, organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Qué puede y qué no puede hacer ahora. Sin nombres de infraestructura.

    Devuelve `hay_incidente=False` cuando todo funciona, en vez de un mensaje
    tranquilizador: una interfaz que siempre muestra algo sobre el estado del
    sistema entrena al cliente a ignorar ese sitio, y el día que importe no lo
    va a mirar.
    """
    panorama = await interruptores.panorama(db, organization_id=organization_id)

    apagadas = [c for c in panorama["capacidades"] if not c["encendida"]]
    if not apagadas and not panorama["solo_lectura"]:
        return {"hay_incidente": False}

    if panorama["solo_lectura"]:
        titulo = "Estamos haciendo una tarea de mantenimiento"
        cuerpo = (
            "Por un rato no se pueden hacer cambios ni crear campañas nuevas. "
            "Todo lo demás sigue disponible."
        )
    else:
        titulo = "Hay una función temporalmente no disponible"
        cuerpo = (
            "Estamos trabajando en ello. Abajo está lo que sí podés hacer "
            "mientras tanto."
        )

    return {
        "hay_incidente": True,
        "titulo": titulo,
        "mensaje": cuerpo,
        # Se nombra en el idioma del cliente, sin la clave interna: `write_back`
        # no le dice nada a nadie fuera del equipo.
        "no_disponible": [
            interruptores._MENSAJE_PARA_EL_CLIENTE.get(c["capacidad"], c["descripcion"])
            for c in apagadas
        ],
        "sigue_disponible": list(SIEMPRE_DISPONIBLE),
    }
