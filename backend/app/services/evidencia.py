"""Evidencia, confianza y registros de lenguaje (spec 11).

Puro. Toma el conjunto comparable que produjo `comparabilidad.py` y responde
tres cosas: qué campañas respaldan la recomendación, cuánta confianza merece, y
cómo se puede hablar de ella.

**La confianza sale del conjunto, no de una impresión.** Cuatro factores:
cuántas campañas comparables hay, cuánto se parecen, cuánto volumen aportan y
qué dispersos son sus costos. Un conjunto de treinta campañas cuyos costos van
de S/ 2 a S/ 90 no es más fiable que uno de cinco consistentes, y una media de
las dos primeras cifras lo diría al revés.

**Tres registros de lenguaje, marcados estructuralmente.**

* *Hecho observado* — está en los datos. "Esta campaña costó S/ 12.40 por
  conversación."
* *Asociación* — dos cosas aparecen juntas en el histórico. "Las campañas con
  video tuvieron mejor costo que las de imagen."
* *Hipótesis* — una explicación posible que los datos no demuestran. "Puede que
  el video explique la diferencia."

La marca es un campo, no un matiz de redacción. Un matiz el LLM lo pierde; un
campo no. Y el lenguaje causal —"el video BAJÓ el costo"— no se admite en
ninguno de los tres: haría falta una prueba controlada, y este alcance no
construye ninguna.
"""
import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.comparabilidad import Comparacion

logger = logging.getLogger("lejovista.evidencia")

# --- Registros de lenguaje --------------------------------------------------

HECHO = "hecho_observado"
ASOCIACION = "asociacion"
HIPOTESIS = "hipotesis"

REGISTROS = (HECHO, ASOCIACION, HIPOTESIS)

ETIQUETA_DEL_REGISTRO = {
    HECHO: "Observado",
    ASOCIACION: "Asociación",
    HIPOTESIS: "Hipótesis",
}

# Cómo debe leerse cada registro. Va con la respuesta: sin esto, "Asociación"
# es una etiqueta que el cliente interpreta como quiere, y lo más probable es
# que la lea como causa.
EXPLICACION_DEL_REGISTRO = {
    HECHO: "Está en los datos de tu cuenta.",
    ASOCIACION: "Estas dos cosas aparecieron juntas en tu histórico. No quiere decir que una cause la otra.",
    HIPOTESIS: "Una explicación posible que los datos no demuestran. Habría que probarla.",
}


@dataclass(frozen=True)
class Afirmacion:
    """Una frase con su registro declarado."""

    texto: str
    registro: str

    def __post_init__(self):
        if self.registro not in REGISTROS:
            raise ValueError(f"Registro desconocido: {self.registro!r}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "texto": self.texto,
            "registro": self.registro,
            "registro_label": ETIQUETA_DEL_REGISTRO[self.registro],
            "registro_explicacion": EXPLICACION_DEL_REGISTRO[self.registro],
        }


def hecho(texto: str) -> Afirmacion:
    return Afirmacion(texto, HECHO)


def asociacion(texto: str) -> Afirmacion:
    return Afirmacion(texto, ASOCIACION)


def hipotesis(texto: str) -> Afirmacion:
    return Afirmacion(texto, HIPOTESIS)


# --- Niveles de confianza ---------------------------------------------------

CONFIANZA_NULA = "sin_evidencia"
CONFIANZA_BAJA = "baja"
CONFIANZA_MEDIA = "media"
CONFIANZA_ALTA = "alta"

NIVELES = (CONFIANZA_NULA, CONFIANZA_BAJA, CONFIANZA_MEDIA, CONFIANZA_ALTA)

TEXTO_DE_LA_CONFIANZA = {
    CONFIANZA_NULA: "Sin evidencia comparable",
    CONFIANZA_BAJA: "Confianza baja",
    CONFIANZA_MEDIA: "Confianza media",
    CONFIANZA_ALTA: "Confianza alta",
}

# Mínimos para que un conjunto pueda sostener una recomendación basada en
# evidencia. Por debajo, la propuesta es exploratoria y se dice.
#
# Tres campañas es poco y se sabe: es el suelo por debajo del cual una mediana
# no es una mediana, es una anécdota con tres puntos. Se eligió tres y no cinco
# porque el riesgo simétrico —dejar a toda cuenta pequeña en exploratorio
# permanente— es real y lo declara la spec.
MINIMO_CAMPANAS = 3
MINIMO_CONVERSACIONES = 15

# Coeficiente de variación (desviación / media) por encima del cual el conjunto
# se considera disperso. 0.5 significa que la desviación típica es la mitad de
# la media: con costos entre S/ 6 y S/ 18 alrededor de S/ 12, la referencia ya
# no describe bien a ninguna campaña concreta.
DISPERSION_ALTA = 0.5


@dataclass(frozen=True)
class Confianza:
    """El nivel, y de dónde sale.

    Los factores viajan con el nivel a propósito: "confianza baja" sin decir
    por qué no le sirve a nadie para decidir si conviene esperar más datos o
    cambiar de enfoque.
    """

    nivel: str
    campanas: int
    conversaciones: int
    grado_medio: float
    dispersion: Optional[float]
    motivos: Tuple[str, ...] = ()

    @property
    def sostiene_recomendacion(self) -> bool:
        """¿Alcanza para una recomendación basada en evidencia? (criterio 7)"""
        return self.nivel in (CONFIANZA_MEDIA, CONFIANZA_ALTA)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nivel": self.nivel,
            "nivel_label": TEXTO_DE_LA_CONFIANZA[self.nivel],
            "campanas_comparables": self.campanas,
            "conversaciones_totales": self.conversaciones,
            "grado_medio": round(self.grado_medio, 4),
            "dispersion": None if self.dispersion is None else round(self.dispersion, 4),
            "motivos": list(self.motivos),
            "sostiene_recomendacion": self.sostiene_recomendacion,
        }


def _dispersion(costos: Sequence[float]) -> Optional[float]:
    """Coeficiente de variación. `None` con menos de dos puntos.

    Relativo y no absoluto: una desviación de S/ 3 es enorme sobre una media de
    S/ 4 y despreciable sobre una de S/ 80.
    """
    validos = [c for c in costos if c and c > 0]
    if len(validos) < 2:
        return None
    media = statistics.fmean(validos)
    if media <= 0:
        return None
    return statistics.stdev(validos) / media


def calcular_confianza(
    comparables: Sequence[Tuple[Dict[str, Any], Comparacion]],
) -> Confianza:
    """Nivel de confianza del conjunto comparable.

    Se degrada, nunca se promueve: se parte de alta y cada carencia baja el
    nivel. Al revés —sumar puntos por virtudes— un conjunto podría compensar
    una dispersión enorme con muchas campañas, y eso es precisamente lo que no
    debe pasar.
    """
    if not comparables:
        return Confianza(
            CONFIANZA_NULA, 0, 0, 0.0, None,
            ("No hay ninguna campaña histórica comparable en esta cuenta.",),
        )

    campanas = len(comparables)
    conversaciones = sum(
        int(c.get("conversaciones") or 0) + int(c.get("leads") or 0)
        for c, _ in comparables
    )
    grado_medio = statistics.fmean(comp.grado for _, comp in comparables)

    costos: List[float] = []
    for campana, _ in comparables:
        resultados = int(campana.get("conversaciones") or 0) + int(campana.get("leads") or 0)
        gasto = float(campana.get("gasto") or 0)
        if resultados > 0 and gasto > 0:
            costos.append(gasto / resultados)
    dispersion = _dispersion(costos)

    nivel = CONFIANZA_ALTA
    motivos: List[str] = []

    if campanas < MINIMO_CAMPANAS:
        nivel = CONFIANZA_BAJA
        motivos.append(
            f"Solo {campanas} campaña(s) comparable(s); hacen falta al menos "
            f"{MINIMO_CAMPANAS} para que la referencia describa algo repetible."
        )
    elif campanas < MINIMO_CAMPANAS * 2:
        nivel = min(nivel, CONFIANZA_MEDIA, key=NIVELES.index)
        motivos.append(f"El conjunto es pequeño: {campanas} campañas comparables.")

    if conversaciones < MINIMO_CONVERSACIONES:
        nivel = CONFIANZA_BAJA
        motivos.append(
            f"Las campañas comparables suman {conversaciones} resultado(s), "
            f"por debajo de {MINIMO_CONVERSACIONES}."
        )

    if dispersion is not None and dispersion > DISPERSION_ALTA:
        nivel = min(nivel, CONFIANZA_BAJA, key=NIVELES.index)
        motivos.append(
            "Los costos de las campañas comparables están muy dispersos entre sí, "
            "así que la referencia no describe bien a ninguna."
        )

    if grado_medio < 0.35:
        nivel = min(nivel, CONFIANZA_BAJA, key=NIVELES.index)
        motivos.append(
            "Las campañas comparables se parecen poco a la propuesta "
            "(temporada, presupuesto o público muy distintos)."
        )

    if not motivos:
        motivos.append(
            f"{campanas} campañas comparables con {conversaciones} resultados y "
            "costos consistentes entre sí."
        )

    return Confianza(nivel, campanas, conversaciones, grado_medio, dispersion, tuple(motivos))


# --- El paquete de evidencia -----------------------------------------------

@dataclass(frozen=True)
class Evidencia:
    """Lo que respalda una propuesta, en la forma en que se muestra y persiste."""

    campanas: Tuple[Dict[str, Any], ...]
    confianza: Confianza
    afirmaciones: Tuple[Afirmacion, ...] = ()
    excluidas: Tuple[Comparacion, ...] = ()
    exploratoria: bool = False
    ventana_dias: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "exploratoria": self.exploratoria,
            "ventana_dias": self.ventana_dias,
            "campanas_comparables": [dict(c) for c in self.campanas],
            "confianza": self.confianza.as_dict(),
            "afirmaciones": [a.as_dict() for a in self.afirmaciones],
            # Las excluidas se resumen por motivo: la lista completa puede tener
            # cientos de filas y lo que se necesita responder es "¿por qué no
            # está aquella?", que se contesta con el motivo.
            "excluidas_por_motivo": _resumen_de_exclusiones(self.excluidas),
        }


def _resumen_de_exclusiones(excluidas: Sequence[Comparacion]) -> List[Dict[str, Any]]:
    conteo: Dict[str, int] = {}
    legible: Dict[str, str] = {}
    for c in excluidas:
        if c.motivo_exclusion is None:
            continue
        conteo[c.motivo_exclusion] = conteo.get(c.motivo_exclusion, 0) + 1
        legible[c.motivo_exclusion] = c.motivo_legible or c.motivo_exclusion
    return [
        {"motivo": m, "motivo_legible": legible[m], "campanas": n}
        for m, n in sorted(conteo.items(), key=lambda kv: -kv[1])
    ]


def _resumen_de_campana(campana: Dict[str, Any], comp: Comparacion) -> Dict[str, Any]:
    """Lo mínimo que hace falta para poder señalarla con el dedo (criterio 1).

    Nombre, periodo y costo por conversación. Sin el periodo, dos campañas del
    mismo nombre en temporadas distintas son indistinguibles; sin el costo, la
    lista no dice por qué respalda nada.
    """
    resultados = int(campana.get("conversaciones") or 0) + int(campana.get("leads") or 0)
    gasto = float(campana.get("gasto") or 0)
    return {
        "campaign_id": campana.get("campaign_id"),
        "nombre": campana.get("nombre"),
        "objetivo": campana.get("objetivo"),
        "desde": _fecha(campana.get("desde") or campana.get("fecha")),
        "hasta": _fecha(campana.get("hasta") or campana.get("fecha")),
        "gasto": round(gasto, 2),
        "resultados": resultados,
        "costo_por_resultado": round(gasto / resultados, 2) if resultados > 0 else None,
        "grado_de_parecido": round(comp.grado, 4),
    }


def _fecha(valor: Any) -> Optional[str]:
    if valor is None:
        return None
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


def construir(
    comparables: Sequence[Tuple[Dict[str, Any], Comparacion]],
    excluidas: Sequence[Comparacion],
    *,
    ventana_dias: Optional[int] = None,
    afirmaciones: Sequence[Afirmacion] = (),
) -> Evidencia:
    """El paquete completo: campañas, confianza, afirmaciones y exclusiones.

    `exploratoria` se decide acá y no en el llamador: es la consecuencia
    directa de la confianza, y dejarla a criterio de cada superficie permitiría
    que una recomendación sin respaldo se presentara como respaldada.
    """
    confianza = calcular_confianza(comparables)
    return Evidencia(
        campanas=tuple(_resumen_de_campana(c, comp) for c, comp in comparables),
        confianza=confianza,
        afirmaciones=tuple(afirmaciones),
        excluidas=tuple(excluidas),
        exploratoria=not confianza.sostiene_recomendacion,
        ventana_dias=ventana_dias,
    )


# --- Buenas prácticas para el modo exploratorio -----------------------------
#
# Lo que sostiene una campaña exploratoria. NO es histórico del cliente —no lo
# hay— y se dice así, porque presentar una buena práctica general como si
# saliera de sus datos es la mentira concreta que el criterio 7 impide.

BUENAS_PRACTICAS = (
    "Empezar con un presupuesto chico y subirlo solo cuando haya resultados medidos.",
    "Una sola audiencia amplia al principio: segmentar de entrada deja a Meta sin datos para optimizar.",
    "Dar al menos 7 días antes de juzgar, que es lo que tarda la entrega en estabilizarse.",
    "Un solo objetivo por campaña, para que el costo por resultado signifique algo.",
)


def afirmaciones_exploratorias() -> Tuple[Afirmacion, ...]:
    """Las afirmaciones de una propuesta sin evidencia comparable.

    Todas van marcadas como hipótesis salvo la primera, que es un hecho sobre
    el estado de la cuenta. Marcarlas como hechos sería exactamente lo que la
    spec prohíbe: presentar una recomendación sin respaldo como si lo tuviera.
    """
    return (
        hecho(
            "Esta cuenta todavía no tiene campañas anteriores comparables con la "
            "que se propone."
        ),
        *(hipotesis(p) for p in BUENAS_PRACTICAS),
    )
