"""El brief mínimo de negocio (spec 12, criterios 8 y 9).

**Un contrato de negocio, no un formulario publicitario.** Lo que hoy se recoge
son parámetros de Meta —objetivo, presupuesto, placements— y esos son la
CONSECUENCIA de una decisión comercial que nadie pregunta. Un cliente que no
sabe de Meta Ads no puede contestar "¿qué optimization_goal querés?", y sí puede
contestar "¿qué querés vender y a quién?".

Los parámetros publicitarios se derivan del brief. No se le preguntan al
cliente.

**La capacidad de atención es un elemento obligatorio, y es el menos obvio.**
Una campaña que genera más conversaciones de las que el negocio puede atender
**empeora** su negocio aunque mejore la métrica: los mensajes sin responder
queman la reputación de su número y el dinero se gasta igual. Sin este dato el
sistema puede optimizar hacia un daño.

**El orden lo trae el cliente.** La conversación reúne los elementos según van
apareciendo; el sistema sabe cuáles faltan y pide los que falten. Un formulario
en orden fijo obliga a quien ya lo contó todo a repetirlo por partes.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("lejovista.chat.brief")

# --- Los elementos del brief ------------------------------------------------

OBJETIVO_COMERCIAL = "objetivo_comercial"
OFERTA = "oferta"
CONDICIONES = "condiciones"
PRESUPUESTO = "presupuesto"
DURACION = "duracion"
PUBLICO = "publico"
DESTINO_WHATSAPP = "destino_whatsapp"
MEDIOS = "medios"
CAPACIDAD_DE_ATENCION = "capacidad_de_atencion"


@dataclass(frozen=True)
class ElementoDelBrief:
    clave: str
    etiqueta: str
    # Lo que el chat pregunta cuando falta. En segunda persona y sin jerga: es
    # el texto que lee un dueño de negocio, no un operador de Ads.
    pregunta: str
    obligatorio: bool = True
    # Por qué hace falta. Solo para los que no son evidentes — pedir un dato sin
    # decir para qué invita a inventarlo.
    porque: Optional[str] = None


BRIEF_MINIMO: Tuple[ElementoDelBrief, ...] = (
    ElementoDelBrief(
        OBJETIVO_COMERCIAL, "Objetivo comercial",
        "¿Qué querés conseguir con esta campaña? Por ejemplo: más consultas por "
        "WhatsApp, vender un producto puntual, o que te conozcan en una zona nueva.",
    ),
    ElementoDelBrief(
        OFERTA, "Oferta",
        "¿Qué estás ofreciendo exactamente? El producto o servicio concreto, con "
        "el precio si lo querés mostrar.",
    ),
    ElementoDelBrief(
        PRESUPUESTO, "Presupuesto",
        "¿Cuánto querés invertir en total en esta campaña?",
    ),
    ElementoDelBrief(
        DURACION, "Duración",
        "¿Cuántos días querés que dure?",
    ),
    ElementoDelBrief(
        DESTINO_WHATSAPP, "WhatsApp de destino",
        "¿A qué número de WhatsApp querés que te escriban?",
    ),
    ElementoDelBrief(
        MEDIOS, "Foto o video",
        "Mandame la foto o el video que querés usar en el anuncio.",
    ),
    ElementoDelBrief(
        CAPACIDAD_DE_ATENCION, "Capacidad de atención",
        "¿Cuántas consultas por día podés atender bien? Si llegan más de las que "
        "podés responder, la campaña te termina jugando en contra.",
        porque=(
            "Una campaña que trae más mensajes de los que podés contestar empeora "
            "tu negocio aunque las cifras se vean bien: la gente que no recibe "
            "respuesta no vuelve, y el dinero se gastó igual."
        ),
    ),
    # Opcionales: mejoran la propuesta y no la bloquean. Exigirlos convertiría
    # el brief en un interrogatorio y haría que el cliente abandonara antes de
    # llegar a la propuesta.
    ElementoDelBrief(
        CONDICIONES, "Condiciones",
        "¿Hay alguna condición que deba figurar? Por ejemplo: stock limitado, "
        "válido hasta cierta fecha, o solo para una zona.",
        obligatorio=False,
    ),
    ElementoDelBrief(
        PUBLICO, "Público o restricciones",
        "¿Hay alguien a quien SÍ o a quien NO querés que le llegue?",
        obligatorio=False,
    ),
)

POR_CLAVE = {e.clave: e for e in BRIEF_MINIMO}
OBLIGATORIOS = tuple(e.clave for e in BRIEF_MINIMO if e.obligatorio)


# Cómo se llenan los elementos del brief desde los slots que ya existen. El
# brief NO duplica el estado de la sesión: se deriva de él, para que no haya dos
# fuentes que puedan discrepar sobre si falta el presupuesto.
_DESDE_SLOTS = {
    OBJETIVO_COMERCIAL: ("goal_text",),
    OFERTA: ("product_name",),
    PRESUPUESTO: ("budget",),
    DURACION: ("days",),
    DESTINO_WHATSAPP: ("whatsapp_number",),
    MEDIOS: ("image_path", "video_path"),
    CAPACIDAD_DE_ATENCION: ("capacidad_de_atencion",),
    CONDICIONES: ("condiciones",),
    PUBLICO: ("publico",),
}


def _tiene_valor(slots: Dict[str, Any], claves: Tuple[str, ...]) -> bool:
    for clave in claves:
        valor = slots.get(clave)
        if valor is None:
            continue
        if isinstance(valor, str) and not valor.strip():
            continue
        if isinstance(valor, (int, float)) and valor <= 0:
            continue
        return True
    return False


@dataclass(frozen=True)
class EstadoDelBrief:
    completos: Tuple[str, ...]
    faltantes: Tuple[str, ...]
    opcionales_faltantes: Tuple[str, ...]

    @property
    def completo(self) -> bool:
        """Sin ningún obligatorio pendiente. Es lo que gobierna el criterio 9."""
        return not self.faltantes

    @property
    def siguiente(self) -> Optional[ElementoDelBrief]:
        """El próximo elemento a pedir, o `None` si no falta ninguno.

        Se pide de uno en uno. Una lista de siete preguntas en un mensaje de
        chat no se contesta: se abandona.
        """
        return POR_CLAVE[self.faltantes[0]] if self.faltantes else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "completo": self.completo,
            "completos": list(self.completos),
            "faltantes": [
                {
                    "clave": c,
                    "etiqueta": POR_CLAVE[c].etiqueta,
                    "pregunta": POR_CLAVE[c].pregunta,
                    "porque": POR_CLAVE[c].porque,
                }
                for c in self.faltantes
            ],
            "opcionales_faltantes": [
                {"clave": c, "etiqueta": POR_CLAVE[c].etiqueta}
                for c in self.opcionales_faltantes
            ],
        }


def estado_del_brief(slots: Dict[str, Any]) -> EstadoDelBrief:
    """Qué tiene y qué le falta a este brief. Derivado de los slots.

    El orden de `faltantes` es el de `BRIEF_MINIMO`, que va de lo más general a
    lo más concreto: preguntar el número de WhatsApp antes de saber qué se
    vende desconcierta a cualquiera.
    """
    slots = slots or {}
    completos, faltantes, opcionales = [], [], []

    for elemento in BRIEF_MINIMO:
        presente = _tiene_valor(slots, _DESDE_SLOTS.get(elemento.clave, (elemento.clave,)))
        if presente:
            completos.append(elemento.clave)
        elif elemento.obligatorio:
            faltantes.append(elemento.clave)
        else:
            opcionales.append(elemento.clave)

    return EstadoDelBrief(tuple(completos), tuple(faltantes), tuple(opcionales))


# --- Extracción de los elementos que no son slots publicitarios -------------
#
# La capacidad de atención llega como texto libre: "puedo atender unas 20 al
# día", "5 o 6", "todas las que lleguen". Se extrae el número, y cuando no hay
# número no se inventa uno: se guarda el texto y se sigue.

_CAPACIDAD_RE = re.compile(
    r"(\d{1,4})\s*(?:consultas?|mensajes?|clientes?|personas?|por\s*d[ií]a|al\s*d[ií]a|diarias?)?",
    re.IGNORECASE,
)


def interpretar_capacidad(texto: Optional[str]) -> Optional[Dict[str, Any]]:
    """Convierte "unas 20 al día" en un dato utilizable.

    Devuelve el texto siempre y el número cuando lo hay. Sin número el brief se
    da por contestado igualmente: "las que sean" es una respuesta legítima de un
    negocio chico, y rechazarla obligaría a inventar una cifra.
    """
    limpio = (texto or "").strip()
    if not limpio:
        return None
    m = _CAPACIDAD_RE.search(limpio)
    numero = None
    if m:
        try:
            candidato = int(m.group(1))
            # Cifras absurdas se descartan como número y se conserva el texto:
            # es más probable que sea un precio o un año mal leído.
            if 1 <= candidato <= 2000:
                numero = candidato
        except (TypeError, ValueError):
            numero = None
    return {"texto": limpio, "consultas_por_dia": numero}


def resumen_para_el_cliente(slots: Dict[str, Any]) -> List[str]:
    """Lo que el sistema entendió del brief, en las palabras del cliente.

    Se le muestra antes de proponer. Un brief mal entendido que nadie revisa se
    convierte en una campaña equivocada, y el momento barato de corregirlo es
    antes de gastar.
    """
    slots = slots or {}
    lineas: List[str] = []

    if slots.get("goal_text"):
        lineas.append(f"**Lo que querés lograr:** {slots['goal_text']}")
    if slots.get("product_name"):
        lineas.append(f"**Lo que ofrecés:** {slots['product_name']}")
    if slots.get("budget"):
        lineas.append(f"**Presupuesto:** {slots['budget']}")
    if slots.get("days"):
        lineas.append(f"**Duración:** {slots['days']} días")
    if slots.get("whatsapp_number"):
        lineas.append(f"**Te escriben al:** {slots['whatsapp_number']}")

    capacidad = slots.get("capacidad_de_atencion")
    if isinstance(capacidad, dict) and capacidad.get("texto"):
        lineas.append(f"**Podés atender:** {capacidad['texto']}")
    elif capacidad:
        lineas.append(f"**Podés atender:** {capacidad}")

    if slots.get("condiciones"):
        lineas.append(f"**Condiciones:** {slots['condiciones']}")
    if slots.get("publico"):
        lineas.append(f"**Público:** {slots['publico']}")

    return lineas
