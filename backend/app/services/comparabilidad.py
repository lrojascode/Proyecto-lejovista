"""Cuándo dos campañas son comparables, y cuánto (spec 11).

Puro: sin BD, sin HTTP, sin LLM. Es la pieza que decide qué histórico puede
sostener una recomendación, así que tiene que ser determinista y explicable —
el LLM no decide qué es comparable (AI-1, ADR-03 extendida).

**Dos condiciones duras y un grado.** Objetivo y evento de conversión iguales:
sin eso las métricas no se pueden poner en la misma escala, y una mediana sobre
campañas de tráfico y de mensajería no describe nada. El resto de dimensiones
—temporada, presupuesto, público, ubicación, formato— produce un GRADO de
parecido entre 0 y 1, que es lo que ordena el conjunto.

**Las incompatibles se excluyen antes de puntuar.** Ponderar hacia cero una
campaña de tráfico frente a una de mensajería la deja en el conjunto con peso
minúsculo, y un conjunto de treinta con veintiocho pesos minúsculos parece
grande. Excluir es más barato y más honesto.

**El grado no es una probabilidad.** Es una ordenación. Decir "0.82 de
parecido" invita a leerlo como una confianza del 82 %, que sería inventarse una
cifra; por eso la confianza se calcula aparte, en `evidencia.py`, a partir del
conjunto entero y no del parecido de un elemento.
"""
import datetime
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("lejovista.comparabilidad")

# --- Evento de conversión ---------------------------------------------------
#
# Lo que la campaña cuenta como resultado. Es la segunda condición dura: dos
# campañas del mismo objetivo pero una optimizada a conversaciones y otra a
# formularios producen costos que no se pueden comparar aunque la etiqueta del
# objetivo coincida.
#
# Se toma de `optimization_goal` cuando el sync lo capturó. Para las campañas
# anteriores a esa columna se DERIVA de qué métrica primaria tuvieron (spec 06:
# conversaciones o leads), que es la misma distinción vista desde el resultado.
# Derivar no es adivinar: es leer el hecho por otra cara.

EVENTO_CONVERSACIONES = "conversaciones"
EVENTO_LEADS = "leads"
EVENTO_DESCONOCIDO = "desconocido"

# `optimization_goal` de Meta → evento del producto.
_GOAL_A_EVENTO = {
    "CONVERSATIONS": EVENTO_CONVERSACIONES,
    "REPLIES": EVENTO_CONVERSACIONES,
    "LEAD_GENERATION": EVENTO_LEADS,
    "QUALITY_LEAD": EVENTO_LEADS,
    "OFFSITE_CONVERSIONS": EVENTO_LEADS,
}


def evento_de_conversion(campana: Dict[str, Any]) -> str:
    """El evento que esta campaña contaba como resultado.

    `desconocido` es un valor legítimo y NO comparable con nada, ni siquiera
    con otro desconocido: dos campañas de las que no sabemos qué contaban no son
    comparables por compartir esa ignorancia.
    """
    goal = (campana.get("optimization_goal") or "").strip().upper()
    if goal in _GOAL_A_EVENTO:
        return _GOAL_A_EVENTO[goal]

    declarado = (campana.get("evento_conversion") or "").strip().lower()
    if declarado in (EVENTO_CONVERSACIONES, EVENTO_LEADS):
        return declarado

    # Derivación por el resultado observado (spec 06).
    conversaciones = int(campana.get("conversaciones") or 0)
    leads = int(campana.get("leads") or 0)
    if conversaciones > 0:
        return EVENTO_CONVERSACIONES
    if leads > 0:
        return EVENTO_LEADS
    return EVENTO_DESCONOCIDO


# --- Motivos de exclusión ---------------------------------------------------

EXCLUIDA_OBJETIVO = "objetivo_distinto"
EXCLUIDA_EVENTO = "evento_de_conversion_distinto"
EXCLUIDA_EVENTO_DESCONOCIDO = "evento_de_conversion_desconocido"
EXCLUIDA_SIN_RESULTADOS = "sin_resultados_medidos"
EXCLUIDA_ES_LA_MISMA = "es_la_campana_de_referencia"

MOTIVOS_LEGIBLES = {
    EXCLUIDA_OBJETIVO: "tiene otro objetivo de campaña",
    EXCLUIDA_EVENTO: "cuenta otro tipo de resultado",
    EXCLUIDA_EVENTO_DESCONOCIDO: "no se sabe qué contaba como resultado",
    EXCLUIDA_SIN_RESULTADOS: "no llegó a registrar ningún resultado",
    EXCLUIDA_ES_LA_MISMA: "es la campaña de referencia",
}


# --- Dimensiones del parecido ----------------------------------------------
#
# Los pesos NO son una calibración empírica: nadie ha medido todavía cuánto
# importa cada dimensión, y presentarlos como calibrados sería exactamente la
# falsa precisión que esta spec ataja. Son un orden de prioridad declarado, y
# están acá para poder discutirlos y cambiarlos con evidencia.

PESO_TEMPORADA = 0.35
PESO_PRESUPUESTO = 0.25
PESO_PUBLICO = 0.20
PESO_FORMATO = 0.10
PESO_UBICACION = 0.10

# A partir de cuántos días de distancia dos campañas dejan de compartir
# temporada. Medio año: pasado ese punto están en estaciones opuestas.
DIAS_TEMPORADA = 180

# Relación de presupuestos por encima de la cual el parecido es cero. Una
# campaña de S/ 100 y una de S/ 2000 no se comportan igual aunque compartan
# objetivo: cambia el aprendizaje del algoritmo de Meta y el alcance.
FACTOR_PRESUPUESTO_MAXIMO = 8.0


def _parecido_temporada(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
    """1.0 el mismo día, 0.0 a `DIAS_TEMPORADA` o más. `None` si falta la fecha.

    `None` y no 0.0: una fecha ausente es desconocimiento, no lejanía. Tratarla
    como lejanía penalizaría a las campañas peor registradas, que es lo
    contrario de lo que se quiere.
    """
    fa, fb = a.get("fecha"), b.get("fecha")
    if not fa or not fb:
        return None
    if isinstance(fa, datetime.datetime):
        fa = fa.date()
    if isinstance(fb, datetime.datetime):
        fb = fb.date()
    dias = abs((fa - fb).days)
    return max(0.0, 1.0 - dias / DIAS_TEMPORADA)


def _parecido_presupuesto(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
    """Cae con el logaritmo de la razón entre presupuestos.

    Logarítmico y no lineal porque lo que importa es el ORDEN de magnitud: la
    diferencia entre 100 y 200 pesa lo mismo que entre 1000 y 2000, y eso es lo
    que se corresponde con cómo cambia el comportamiento de la entrega.
    """
    ga, gb = float(a.get("gasto") or 0), float(b.get("gasto") or 0)
    if ga <= 0 or gb <= 0:
        return None
    razon = max(ga, gb) / min(ga, gb)
    if razon >= FACTOR_PRESUPUESTO_MAXIMO:
        return 0.0
    return max(0.0, 1.0 - math.log(razon) / math.log(FACTOR_PRESUPUESTO_MAXIMO))


def _parecido_conjunto(a: Optional[Sequence[str]], b: Optional[Sequence[str]]) -> Optional[float]:
    """Jaccard entre dos conjuntos de etiquetas. `None` si alguno falta.

    Sirve para público, ubicación y formato: los tres se describen como
    conjuntos de etiquetas y ninguno tiene un orden natural que permita una
    distancia mejor.
    """
    if a is None or b is None:
        return None
    sa, sb = {str(x).strip().lower() for x in a if x}, {str(x).strip().lower() for x in b if x}
    if not sa and not sb:
        return None
    union = sa | sb
    if not union:
        return None
    return len(sa & sb) / len(union)


@dataclass(frozen=True)
class Comparacion:
    """El resultado de comparar una campaña histórica con la de referencia."""

    campaign_id: str
    comparable: bool
    grado: float = 0.0
    motivo_exclusion: Optional[str] = None
    # Qué dimensiones se pudieron evaluar y cuáles no. Es lo que permite
    # explicar un grado bajo sin abrir el código.
    dimensiones: Dict[str, Optional[float]] = field(default_factory=dict)

    @property
    def motivo_legible(self) -> Optional[str]:
        if self.motivo_exclusion is None:
            return None
        return MOTIVOS_LEGIBLES.get(self.motivo_exclusion, self.motivo_exclusion)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "comparable": self.comparable,
            "grado": round(self.grado, 4),
            "motivo_exclusion": self.motivo_exclusion,
            "motivo_legible": self.motivo_legible,
            "dimensiones": {k: (None if v is None else round(v, 4)) for k, v in self.dimensiones.items()},
        }


def comparar(referencia: Dict[str, Any], candidata: Dict[str, Any]) -> Comparacion:
    """¿Es `candidata` comparable con `referencia`, y cuánto se parecen?

    Las condiciones duras se evalúan primero y cortan: puntuar algo que ya se
    sabe incomparable solo produce un número que alguien puede terminar
    mirando.
    """
    cid = str(candidata.get("campaign_id") or "")

    if cid and cid == str(referencia.get("campaign_id") or ""):
        return Comparacion(cid, False, motivo_exclusion=EXCLUIDA_ES_LA_MISMA)

    obj_ref = (referencia.get("objetivo") or "").strip().upper()
    obj_can = (candidata.get("objetivo") or "").strip().upper()
    if obj_ref and obj_can and obj_ref != obj_can:
        return Comparacion(cid, False, motivo_exclusion=EXCLUIDA_OBJETIVO)

    ev_ref = evento_de_conversion(referencia)
    ev_can = evento_de_conversion(candidata)
    if ev_can == EVENTO_DESCONOCIDO or ev_ref == EVENTO_DESCONOCIDO:
        return Comparacion(cid, False, motivo_exclusion=EXCLUIDA_EVENTO_DESCONOCIDO)
    if ev_ref != ev_can:
        return Comparacion(cid, False, motivo_exclusion=EXCLUIDA_EVENTO)

    # Una campaña sin resultados no puede sostener nada: su costo por resultado
    # no existe, así que no aporta al baseline ni a la comparación.
    resultados = int(candidata.get("conversaciones") or 0) + int(candidata.get("leads") or 0)
    if resultados <= 0:
        return Comparacion(cid, False, motivo_exclusion=EXCLUIDA_SIN_RESULTADOS)

    dimensiones: Dict[str, Optional[float]] = {
        "temporada": _parecido_temporada(referencia, candidata),
        "presupuesto": _parecido_presupuesto(referencia, candidata),
        "publico": _parecido_conjunto(referencia.get("publico"), candidata.get("publico")),
        "formato": _parecido_conjunto(referencia.get("formato"), candidata.get("formato")),
        "ubicacion": _parecido_conjunto(referencia.get("ubicacion"), candidata.get("ubicacion")),
    }
    pesos = {
        "temporada": PESO_TEMPORADA,
        "presupuesto": PESO_PRESUPUESTO,
        "publico": PESO_PUBLICO,
        "formato": PESO_FORMATO,
        "ubicacion": PESO_UBICACION,
    }

    # Las dimensiones que no se pudieron evaluar se sacan del promedio en vez de
    # contarse como cero. Si no, una campaña vieja bien parecida en lo que sí se
    # sabe quedaría por debajo de una reciente mediocre, solo porque de la vieja
    # se registró menos.
    evaluadas = {k: v for k, v in dimensiones.items() if v is not None}
    if not evaluadas:
        # Comparable por las condiciones duras y sin nada más que decir. Grado
        # neutro: ni se premia ni se castiga lo que no se sabe.
        grado = 0.5
    else:
        peso_total = sum(pesos[k] for k in evaluadas)
        grado = sum(pesos[k] * v for k, v in evaluadas.items()) / peso_total

    return Comparacion(cid, True, grado=grado, dimensiones=dimensiones)


def conjunto_comparable(
    referencia: Dict[str, Any],
    candidatas: Sequence[Dict[str, Any]],
    *,
    grado_minimo: float = 0.0,
    limite: Optional[int] = None,
) -> Tuple[List[Tuple[Dict[str, Any], Comparacion]], List[Comparacion]]:
    """Ordena las candidatas por parecido y devuelve `(incluidas, excluidas)`.

    Se devuelven también las excluidas, con su motivo. Un conjunto comparable
    que solo muestra lo que entró no permite responder "¿y por qué no está
    aquella campaña?", que es la primera pregunta de cualquier operador que
    conozca la cuenta.

    `grado_minimo` es 0 por defecto: la exclusión la hacen las condiciones
    duras, no un umbral de parecido. Un umbral alto deja a las cuentas pequeñas
    en modo exploratorio permanente, que es el riesgo declarado de esta spec.
    """
    incluidas: List[Tuple[Dict[str, Any], Comparacion]] = []
    excluidas: List[Comparacion] = []

    for candidata in candidatas:
        resultado = comparar(referencia, candidata)
        if resultado.comparable and resultado.grado >= grado_minimo:
            incluidas.append((candidata, resultado))
        else:
            excluidas.append(resultado)

    # Orden estable: por grado descendente y, a igualdad, por campaign_id, para
    # que dos ejecuciones sobre los mismos datos produzcan el mismo orden. Sin
    # el desempate, la evidencia mostrada podría cambiar entre recargas.
    incluidas.sort(key=lambda par: (-par[1].grado, par[1].campaign_id))
    if limite is not None:
        incluidas = incluidas[:limite]
    return incluidas, excluidas
