"""Qué hallazgos merecen interrumpir, y con qué frecuencia (spec 12, 17 y 23).

**No hay un segundo motor de detección.** La capa de análisis determinista ya
produce `AIInsight`; el chat es su superficie de entrega para el cliente.
Inventar aquí otra detección produciría dos verdades sobre la misma cuenta que
podrían contradecirse, y la que el cliente vería sería la peor mantenida.

Lo que esta spec añade es la **selección** y el **límite de frecuencia**. Es el
riesgo declarado y no es teórico: un hallazgo vigente lo sigue siendo todos los
días hasta que alguien lo toque, así que sin límite el mismo aviso saldría en
cada apertura. En tres aperturas el cliente deja de leerlos, y con ellos se
pierde el aviso que sí importaba.

**La ventana depende del nivel, no del gusto.** Una campaña gastando sin ninguna
conversión merece insistir cada día; una observación de oportunidad no merece
más de una vez por semana. La misma ventana para los dos convertiría lo urgente
en ruido o lo accesorio en insistencia.
"""
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("lejovista.chat.avisos")

# Cuántos avisos como mucho en una apertura. Tres es el punto en que todavía se
# leen: una lista de ocho se cierra entera sin mirar cuál era el importante.
MAX_POR_APERTURA = 3

# Ventana de repetición por nivel, en horas. La conservadora es la de INFO, y
# la spec pide empezar por ahí y relajar con evidencia de uso.
VENTANA_POR_NIVEL = {
    "CRITICAL": 24,
    "WARNING": 72,
    "INFO": 168,  # una semana
}
VENTANA_POR_DEFECTO = 168

# Categorías que NO se entregan al cliente. No es censura: son hallazgos cuya
# acción corresponde a la agencia —tocar placements, ajustar segmentación— y
# entregárselos al cliente le da un problema que no puede resolver.
CATEGORIAS_DE_AGENCIA = frozenset({"PLACEMENT"})

# Orden de interrupción. Lo que cuesta dinero ahora va antes que lo que podría
# mejorar mañana.
ORDEN_DE_NIVEL = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


@dataclass(frozen=True)
class Aviso:
    """Un hallazgo listo para entregarse en la conversación."""

    huella: str
    categoria: str
    nivel: str
    titulo: str
    descripcion: str
    insight_id: Optional[str] = None
    campaign_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "huella": self.huella,
            "categoria": self.categoria,
            "nivel": self.nivel,
            "titulo": self.titulo,
            "descripcion": self.descripcion,
            "insight_id": self.insight_id,
            "campaign_id": self.campaign_id,
        }


def huella_de(insight: Any) -> str:
    """De qué habla el aviso, con independencia de cómo esté redactado hoy.

    Categoría más sujeto. El texto lleva las cifras del día y cambia solo; si la
    huella dependiera del texto, un céntimo de diferencia lo convertiría en un
    aviso nuevo y el límite de frecuencia no serviría para nada.
    """
    categoria = getattr(insight, "category", None) or "GENERAL"
    sujeto = getattr(insight, "campaign_id", None) or "cuenta"
    return f"{categoria}:{sujeto}"


def ventana_horas(nivel: Optional[str]) -> int:
    return VENTANA_POR_NIVEL.get((nivel or "").upper(), VENTANA_POR_DEFECTO)


def seleccionar(
    insights: Sequence[Any],
    entregados: Dict[str, datetime.datetime],
    *,
    ahora: Optional[datetime.datetime] = None,
    maximo: int = MAX_POR_APERTURA,
) -> tuple:
    """Devuelve `(a_entregar, descartados_por_frecuencia)`.

    Los descartados se devuelven, no se tiran: la proporción de avisos que se
    silencian por frecuencia es lo que dice si la ventana está bien puesta. Si
    se descarta casi todo, la ventana ahoga; si no se descarta nada, no está
    haciendo nada.
    """
    ahora = ahora or datetime.datetime.utcnow()
    a_entregar: List[Aviso] = []
    descartados: List[Aviso] = []

    candidatos = [
        i for i in insights
        if not getattr(i, "is_dismissed", False)
        and (getattr(i, "category", None) or "") not in CATEGORIAS_DE_AGENCIA
    ]
    candidatos.sort(
        key=lambda i: (
            ORDEN_DE_NIVEL.get((getattr(i, "level", "") or "").upper(), 9),
            getattr(i, "created_at", None) or datetime.datetime.min,
        )
    )

    for insight in candidatos:
        aviso = Aviso(
            huella=huella_de(insight),
            categoria=getattr(insight, "category", None) or "GENERAL",
            nivel=(getattr(insight, "level", None) or "INFO").upper(),
            titulo=getattr(insight, "title", "") or "",
            descripcion=getattr(insight, "description", "") or "",
            insight_id=getattr(insight, "id", None),
            campaign_id=getattr(insight, "campaign_id", None),
        )

        ultimo = entregados.get(aviso.huella)
        if ultimo is not None:
            horas = (ahora - ultimo).total_seconds() / 3600
            if horas < ventana_horas(aviso.nivel):
                descartados.append(aviso)
                continue

        if len(a_entregar) >= maximo:
            # Se corta por tope, no por frecuencia. Anotarlo como descartado por
            # frecuencia mentiría en la única métrica que dice si la ventana
            # está bien calibrada.
            break

        a_entregar.append(aviso)

    return a_entregar, descartados


def redactar(avisos: Sequence[Aviso]) -> Optional[str]:
    """El texto del bloque de avisos. Determinista, como el resumen.

    Se usa el título y la descripción que el análisis ya escribió: reescribirlos
    aquí produciría dos versiones del mismo hallazgo, y la que el cliente leería
    sería la que nadie mantiene.
    """
    if not avisos:
        return None

    encabezado = (
        "Además, hay algo que conviene que veas:"
        if len(avisos) == 1 else
        "Además, hay un par de cosas que conviene que veas:"
    )
    lineas = [encabezado]
    for aviso in avisos:
        cuerpo = aviso.descripcion.strip()
        lineas.append(f"- **{aviso.titulo.strip()}** — {cuerpo}" if cuerpo
                      else f"- **{aviso.titulo.strip()}**")
    return "\n".join(lineas)
