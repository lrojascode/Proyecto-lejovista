"""Metrica primaria, baseline y estados de campana (spec 06).

Define lo que el producto entiende por "esto funciono". Tres correcciones sobre
lo que habia:

1. **Muestras insuficientes producian etiquetas definitivas.** El clasificador
   calificaba una campana sin exigir un minimo de dias ni de conversaciones: una
   campana con UNA conversacion barata salia ganadora, y esa etiqueta alimentaba
   las recomendaciones siguientes.

2. **El baseline era sensible a valores extremos.** El CPL objetivo se calibraba
   con un promedio, que un punado de campanas anomalas desplaza. Se pasa a la
   MEDIANA, y se conserva ademas el volumen y el gasto del conjunto: una mediana
   sobre tres campanas diminutas y una sobre treinta campanas grandes son cifras
   distintas aunque coincidan.

3. **No habia vocabulario comun.** Las etiquetas del clasificador
   (GANADORA/BUENA/REGULAR/CARA/PAUSAR) siguen existiendo para la agencia; los
   cuatro estados de aca son lo que ve el cliente, y ninguno es definitivo sobre
   una muestra pequena.

Todo determinista (AI-1): ninguna cifra de este modulo puede salir de un LLM.
"""
import datetime
import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("lejovista.baseline")

# --- Los cuatro estados -----------------------------------------------------
#
# Se evita deliberadamente una quinta categoria "fallida": con muestra pequena no
# se puede afirmar el fracaso mas de lo que se puede afirmar el exito.

DATOS_INSUFICIENTES = "datos_insuficientes"
PROMETEDORA = "prometedora"
VALIDADA = "validada"
POR_DEBAJO_DEL_BASELINE = "por_debajo_del_baseline"

ESTADOS = (DATOS_INSUFICIENTES, PROMETEDORA, VALIDADA, POR_DEBAJO_DEL_BASELINE)

TEXTO_DEL_ESTADO = {
    DATOS_INSUFICIENTES: "Datos insuficientes",
    PROMETEDORA: "Prometedora",
    VALIDADA: "Validada",
    POR_DEBAJO_DEL_BASELINE: "Por debajo del baseline",
}

# --- Origen de la metrica primaria ------------------------------------------
#
# La conversion relevante son las conversaciones de WhatsApp atribuidas por Meta
# (ADR-09). Cuando la campana no es de mensajeria se cae a leads — y la respuesta
# DECLARA cual de las dos se uso: mezclarlas sin decirlo produce comparaciones
# falsas entre campanas de distinta naturaleza.

ORIGEN_CONVERSACIONES = "conversaciones"
ORIGEN_LEADS = "leads"


@dataclass(frozen=True)
class CostoPorConversacion:
    """Metrica primaria de una campana, con su origen declarado."""

    valor: Optional[float]
    conversiones: int
    gasto: float
    origen: str  # ORIGEN_CONVERSACIONES | ORIGEN_LEADS

    @property
    def hay_metrica(self) -> bool:
        return self.valor is not None


def costo_por_conversacion(
    *, gasto: float, conversaciones: int, leads: int = 0
) -> CostoPorConversacion:
    """Gasto dividido entre conversaciones de WhatsApp atribuidas por Meta.

    **Nunca se presenta como venta, ingreso ni retorno.** Una conversacion es
    alguien que escribio, no alguien que compro; confundirlas es prometer un
    resultado comercial que el dato no sostiene.
    """
    if conversaciones > 0:
        origen, n = ORIGEN_CONVERSACIONES, conversaciones
    elif leads > 0:
        origen, n = ORIGEN_LEADS, leads
    else:
        return CostoPorConversacion(None, 0, float(gasto), ORIGEN_CONVERSACIONES)
    return CostoPorConversacion(round(float(gasto) / n, 2), n, float(gasto), origen)


# --- Madurez ----------------------------------------------------------------


@dataclass(frozen=True)
class UmbralesDeMadurez:
    """Cuando una campana tiene muestra suficiente para juzgarla.

    Condicion DOBLE y configurable por Organizacion: el volumen razonable depende
    del rubro. El gasto minimo cierra el caso de la unica conversacion barata,
    que cumple los otros dos y no significa nada.
    """

    dias_minimos: int = 7
    conversaciones_minimas: int = 10
    gasto_minimo: float = 50.0


DEFAULT_MADUREZ = UmbralesDeMadurez()


def get_madurez_for_org(organization: Any = None) -> UmbralesDeMadurez:
    overrides = getattr(organization, "ai_thresholds_overrides", None) or {}
    return UmbralesDeMadurez(
        dias_minimos=int(overrides.get("madurez_dias_minimos", DEFAULT_MADUREZ.dias_minimos)),
        conversaciones_minimas=int(
            overrides.get("madurez_conversaciones_minimas", DEFAULT_MADUREZ.conversaciones_minimas)
        ),
        gasto_minimo=float(overrides.get("madurez_gasto_minimo", DEFAULT_MADUREZ.gasto_minimo)),
    )


def es_madura(
    *, dias: int, conversaciones: int, gasto: float, umbrales: UmbralesDeMadurez = DEFAULT_MADUREZ
) -> bool:
    return (
        dias >= umbrales.dias_minimos
        and conversaciones >= umbrales.conversaciones_minimas
        and gasto >= umbrales.gasto_minimo
    )


def motivo_de_inmadurez(
    *, dias: int, conversaciones: int, gasto: float, umbrales: UmbralesDeMadurez = DEFAULT_MADUREZ
) -> Optional[str]:
    """Que falta para poder juzgar. En lenguaje de cliente, no de umbral."""
    faltan = []
    if dias < umbrales.dias_minimos:
        faltan.append(f"{umbrales.dias_minimos - dias} día(s) más de recorrido")
    if conversaciones < umbrales.conversaciones_minimas:
        faltan.append(
            f"{umbrales.conversaciones_minimas - conversaciones} conversación(es) más"
        )
    if gasto < umbrales.gasto_minimo:
        faltan.append(f"algo más de inversión acumulada")
    return "Falta " + ", ".join(faltan) + " para poder juzgarla." if faltan else None


# --- Baseline ---------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """Referencia contra la que se compara una campana.

    Lleva SIEMPRE el tamano del conjunto, el volumen y el gasto: un baseline
    suelto no permite distinguir una referencia solida de una anecdota, y quien
    lo consuma tiene que poder hacerlo.
    """

    mediana: Optional[float]
    campanas_comparables: int
    conversaciones_totales: int
    gasto_total: float
    origen: str = ORIGEN_CONVERSACIONES
    # Estacion/variante con la que se calculo. Permite recalibrar sin alterar la
    # evaluacion historica de una campana ya creada.
    etiqueta: Optional[str] = None
    calculado_en: Optional[str] = None

    @property
    def hay_baseline(self) -> bool:
        return self.mediana is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mediana": self.mediana,
            "campanas_comparables": self.campanas_comparables,
            "conversaciones_totales": self.conversaciones_totales,
            "gasto_total": round(self.gasto_total, 2),
            "origen": self.origen,
            "etiqueta": self.etiqueta,
            "calculado_en": self.calculado_en,
        }


# Un costo por conversacion mas de N veces la mediana del conjunto es una
# anomalia: entra en el conjunto y desplazaria la referencia sin describir nada
# reproducible. Se excluye del CALCULO, no del reporte.
FACTOR_ANOMALIA = 5.0


def calcular_baseline(
    campanas: Sequence[Dict[str, Any]],
    *,
    umbrales: UmbralesDeMadurez = DEFAULT_MADUREZ,
    etiqueta: Optional[str] = None,
) -> Baseline:
    """Mediana del costo por conversacion de las campanas comparables.

    Cada elemento de `campanas` necesita `gasto`, `conversaciones`, `dias` y
    opcionalmente `leads`.

    Dos exclusiones antes de calcular:

    * **Muestras insuficientes.** Una campana que todavia no se puede juzgar
      tampoco puede servir de referencia para juzgar a otras.
    * **Anomalias.** Un valor extremo entra en el conjunto y desplaza la
      referencia sin describir nada reproducible.

    Mediana y no promedio: el promedio lo mueve un solo valor extremo, y la
    referencia contra la que se decide gasto no puede depender de eso.
    """
    utiles = []
    for c in campanas:
        metrica = costo_por_conversacion(
            gasto=float(c.get("gasto") or 0),
            conversaciones=int(c.get("conversaciones") or 0),
            leads=int(c.get("leads") or 0),
        )
        if not metrica.hay_metrica:
            continue
        if not es_madura(
            dias=int(c.get("dias") or 0),
            conversaciones=metrica.conversiones,
            gasto=metrica.gasto,
            umbrales=umbrales,
        ):
            continue
        utiles.append(metrica)

    if not utiles:
        return Baseline(None, 0, 0, 0.0, etiqueta=etiqueta,
                        calculado_en=datetime.datetime.utcnow().isoformat())

    valores = [m.valor for m in utiles]
    mediana_bruta = statistics.median(valores)
    sin_anomalias = [m for m in utiles if m.valor <= mediana_bruta * FACTOR_ANOMALIA]
    if not sin_anomalias:
        sin_anomalias = utiles

    origen = (
        ORIGEN_CONVERSACIONES
        if any(m.origen == ORIGEN_CONVERSACIONES for m in sin_anomalias)
        else ORIGEN_LEADS
    )
    return Baseline(
        mediana=round(statistics.median([m.valor for m in sin_anomalias]), 2),
        campanas_comparables=len(sin_anomalias),
        conversaciones_totales=sum(m.conversiones for m in sin_anomalias),
        gasto_total=sum(m.gasto for m in sin_anomalias),
        origen=origen,
        etiqueta=etiqueta,
        calculado_en=datetime.datetime.utcnow().isoformat(),
    )


# --- Estado de una campana --------------------------------------------------

# Cuanto mejor que el baseline tiene que ir una campana inmadura para llamarla
# prometedora. No es una promesa: es "va bien por ahora, todavia no alcanza".
MARGEN_PROMETEDORA = 0.9

# Y cuanta evidencia hace falta ANTES de poder decir siquiera eso. Sin este piso,
# una campana con UNA conversacion barata salia "prometedora" — que es la lectura
# optimista que esta spec existe para evitar. Una sola conversacion no es una
# tendencia favorable, es una conversacion.
PISO_PARA_PROMETEDORA = 0.4


@dataclass(frozen=True)
class EstadoDeCampana:
    estado: str
    texto: str
    costo_por_conversacion: Optional[float]
    origen: str
    baseline: Optional[float]
    motivo: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "estado": self.estado,
            "texto": self.texto,
            "costo_por_conversacion": self.costo_por_conversacion,
            "origen_de_la_conversion": self.origen,
            "baseline": self.baseline,
            "motivo": self.motivo,
        }


def evaluar_campana(
    *,
    gasto: float,
    conversaciones: int,
    dias: int,
    leads: int = 0,
    baseline: Optional[Baseline] = None,
    umbrales: UmbralesDeMadurez = DEFAULT_MADUREZ,
) -> EstadoDeCampana:
    """Uno de los cuatro estados. Nunca una etiqueta definitiva sobre muestra pequena.

    El orden de las comprobaciones ES la regla: la madurez se mira ANTES que el
    rendimiento, porque sin muestra suficiente el rendimiento no significa nada
    — ni para bien ni para mal.
    """
    metrica = costo_por_conversacion(gasto=gasto, conversaciones=conversaciones, leads=leads)
    ref = baseline.mediana if (baseline and baseline.hay_baseline) else None

    if not metrica.hay_metrica:
        return EstadoDeCampana(
            DATOS_INSUFICIENTES, TEXTO_DEL_ESTADO[DATOS_INSUFICIENTES],
            None, metrica.origen, ref,
            motivo="Todavía no hay ninguna conversación atribuida.",
        )

    madura = es_madura(
        dias=dias, conversaciones=metrica.conversiones, gasto=metrica.gasto, umbrales=umbrales
    )

    if not madura:
        # Con muestra insuficiente solo caben dos lecturas, y ninguna es un
        # veredicto: va bien por ahora, o todavia no se puede decir nada.
        hay_indicio = (
            metrica.conversiones >= max(1, int(umbrales.conversaciones_minimas * PISO_PARA_PROMETEDORA))
            and metrica.gasto >= umbrales.gasto_minimo * PISO_PARA_PROMETEDORA
        )
        if hay_indicio and ref is not None and metrica.valor <= ref * MARGEN_PROMETEDORA:
            return EstadoDeCampana(
                PROMETEDORA, TEXTO_DEL_ESTADO[PROMETEDORA],
                metrica.valor, metrica.origen, ref,
                motivo=motivo_de_inmadurez(
                    dias=dias, conversaciones=metrica.conversiones,
                    gasto=metrica.gasto, umbrales=umbrales,
                ),
            )
        return EstadoDeCampana(
            DATOS_INSUFICIENTES, TEXTO_DEL_ESTADO[DATOS_INSUFICIENTES],
            metrica.valor, metrica.origen, ref,
            motivo=motivo_de_inmadurez(
                dias=dias, conversaciones=metrica.conversiones,
                gasto=metrica.gasto, umbrales=umbrales,
            ),
        )

    if ref is None:
        # Madura pero sin con que compararla. Decirlo es mejor que inventar una
        # referencia: la campana esta medida, lo que falta es el contexto.
        return EstadoDeCampana(
            DATOS_INSUFICIENTES, TEXTO_DEL_ESTADO[DATOS_INSUFICIENTES],
            metrica.valor, metrica.origen, None,
            motivo="No hay campañas comparables con las que establecer un baseline.",
        )

    if metrica.valor <= ref:
        return EstadoDeCampana(
            VALIDADA, TEXTO_DEL_ESTADO[VALIDADA], metrica.valor, metrica.origen, ref,
        )
    return EstadoDeCampana(
        POR_DEBAJO_DEL_BASELINE, TEXTO_DEL_ESTADO[POR_DEBAJO_DEL_BASELINE],
        metrica.valor, metrica.origen, ref,
    )
