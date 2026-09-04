"""El resumen con el que abre el chat (spec 12, criterios 1, 2 y 6).

**El chat abre con contenido, no con un cursor.** La spec maestra hace del chat
el punto principal de valor para un cliente que no sabe de Meta Ads, y un cursor
parpadeante le exige justo lo que no tiene: saber qué preguntar.

**Determinista de punta a punta.** El resumen se construye con datos ya
sincronizados y el LLM, si está, solo lo redacta. Así existe aunque el modelo no
responda — que es el criterio 2 y también la diferencia entre un producto que
degrada y uno que se queda en blanco.

**Usa los cuatro estados de `06`, no etiquetas propias.** "Validada" y
"prometedora" significan algo definido y con muestra mínima detrás; "va bien" no
significa nada y sobre tres conversaciones sería mentira. El chat presenta lo
que el clasificador produce y no clasifica por su cuenta (ADR-10 preservada).

**No dispara análisis.** Consume lo que el análisis agendado ya dejó; abrir una
conversación no puede provocar trabajo pesado ni saltarse el debounce de la
ADR-07.
"""
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.services.baseline import (
    DATOS_INSUFICIENTES,
    POR_DEBAJO_DEL_BASELINE,
    PROMETEDORA,
    TEXTO_DEL_ESTADO,
    VALIDADA,
    Baseline,
    evaluar_campana,
)
from app.utils.formatting import money

logger = logging.getLogger("lejovista.chat.resumen")

# Cuántas campañas se nombran en el resumen. Más de tres deja de ser un resumen
# y pasa a ser la tabla que el cliente puede abrir cuando quiera.
MAX_CAMPANAS_NOMBRADAS = 3

# Ventana del resumen de apertura. Coincide con la que muestran el panel y la
# lista de campañas: si dijeran periodos distintos, el cliente vería dos cifras
# que no cuadran y ninguna forma de saber cuál mirar.
VENTANA_DIAS = 7


@dataclass(frozen=True)
class ResumenDeApertura:
    """Lo que el chat cuenta sin que le pregunten."""

    hay_actividad: bool
    gasto: float = 0.0
    resultados: int = 0
    costo_por_resultado: Optional[float] = None
    por_estado: Dict[str, int] = None
    destacadas: tuple = ()
    ventana_dias: int = VENTANA_DIAS

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hay_actividad": self.hay_actividad,
            "ventana_dias": self.ventana_dias,
            "gasto": round(self.gasto, 2),
            "resultados": self.resultados,
            "costo_por_resultado": self.costo_por_resultado,
            "por_estado": dict(self.por_estado or {}),
            "destacadas": [dict(c) for c in self.destacadas],
        }


def construir(
    campanas: Sequence[Dict[str, Any]],
    *,
    baseline: Optional[Baseline] = None,
    ventana_dias: int = VENTANA_DIAS,
) -> ResumenDeApertura:
    """Agrega las campañas del periodo y las clasifica con los estados de `06`.

    `campanas` son filas ya agregadas por campaña, la misma forma que produce
    `baseline_repository.campanas_agregadas`. No se consulta nada aquí: esta
    función es pura para poder probarla con datasets.
    """
    if not campanas:
        return ResumenDeApertura(hay_actividad=False, por_estado={}, ventana_dias=ventana_dias)

    gasto_total = sum(float(c.get("gasto") or 0) for c in campanas)
    resultados_total = sum(
        int(c.get("conversaciones") or 0) + int(c.get("leads") or 0) for c in campanas
    )

    por_estado: Dict[str, int] = {}
    evaluadas = []
    for campana in campanas:
        estado = evaluar_campana(
            gasto=float(campana.get("gasto") or 0),
            conversaciones=int(campana.get("conversaciones") or 0),
            leads=int(campana.get("leads") or 0),
            dias=int(campana.get("dias") or 0),
            baseline=baseline,
        )
        por_estado[estado.estado] = por_estado.get(estado.estado, 0) + 1
        evaluadas.append((campana, estado))

    # Se destaca lo que merece atención, en este orden: lo que está por debajo
    # del baseline primero —es donde se pierde dinero— y después lo validado,
    # que es lo que conviene sostener. Lo inmaduro no se destaca: sobre muestra
    # corta no hay nada que decir todavía.
    prioridad = {POR_DEBAJO_DEL_BASELINE: 0, VALIDADA: 1, PROMETEDORA: 2}
    interesantes = [
        (c, e) for c, e in evaluadas
        if e.estado in (POR_DEBAJO_DEL_BASELINE, VALIDADA, PROMETEDORA)
    ]
    interesantes.sort(key=lambda par: (prioridad.get(par[1].estado, 9), par[0].get("nombre") or ""))

    destacadas = tuple(
        {
            "campaign_id": c.get("campaign_id"),
            "nombre": c.get("nombre"),
            "estado": e.estado,
            "estado_label": TEXTO_DEL_ESTADO.get(e.estado, e.estado),
            "costo_por_resultado": e.costo_por_conversacion,
            "motivo": e.motivo,
        }
        for c, e in interesantes[:MAX_CAMPANAS_NOMBRADAS]
    )

    return ResumenDeApertura(
        hay_actividad=gasto_total > 0 or resultados_total > 0,
        gasto=gasto_total,
        resultados=resultados_total,
        costo_por_resultado=(
            round(gasto_total / resultados_total, 2) if resultados_total > 0 else None
        ),
        por_estado=por_estado,
        destacadas=destacadas,
        ventana_dias=ventana_dias,
    )


def redactar(resumen: ResumenDeApertura) -> str:
    """El texto determinista del resumen. Existe aunque no haya LLM.

    Escrito para un dueño de negocio: sin siglas, sin CPL, sin "optimización".
    Cada cifra sale de `resumen`, ninguna se calcula aquí — calcular en la capa
    de redacción es como se cuelan las cifras que no cuadran con la tabla.
    """
    if not resumen.hay_actividad:
        return (
            f"En los últimos {resumen.ventana_dias} días no hubo movimiento en tu "
            "cuenta: ninguna campaña gastó ni trajo consultas.\n\n"
            "Si esperabas que hubiera, puede que estén pausadas. Preguntame por el "
            "estado de tus campañas y te lo reviso."
        )

    partes = [
        f"Esto es lo que pasó en los últimos {resumen.ventana_dias} días."
    ]

    if resumen.resultados > 0 and resumen.costo_por_resultado is not None:
        partes.append(
            f"Invertiste **{money(resumen.gasto)}** y llegaron "
            f"**{resumen.resultados}** consultas, a "
            f"**{money(resumen.costo_por_resultado)}** cada una."
        )
    else:
        partes.append(
            f"Invertiste **{money(resumen.gasto)}** y todavía no llegó ninguna "
            "consulta en este periodo."
        )

    por_debajo = (resumen.por_estado or {}).get(POR_DEBAJO_DEL_BASELINE, 0)
    validadas = (resumen.por_estado or {}).get(VALIDADA, 0)
    insuficientes = (resumen.por_estado or {}).get(DATOS_INSUFICIENTES, 0)

    estado_frases = []
    if validadas:
        estado_frases.append(
            f"{validadas} campaña(s) con resultados sostenidos"
        )
    if por_debajo:
        estado_frases.append(
            f"{por_debajo} por debajo de lo habitual en tu cuenta"
        )
    if insuficientes:
        estado_frases.append(
            f"{insuficientes} todavía sin datos suficientes para juzgarlas"
        )
    if estado_frases:
        partes.append("Tenés " + ", y ".join(estado_frases) + ".")

    for campana in resumen.destacadas:
        costo = (
            f" ({money(campana['costo_por_resultado'])} por consulta)"
            if campana.get("costo_por_resultado") else ""
        )
        partes.append(
            f"- **{campana['nombre']}** — {campana['estado_label']}{costo}."
        )

    partes.append("¿Querés que profundice en alguna, o preferís armar una campaña nueva?")
    return "\n\n".join(partes)


def respuestas_rapidas(resumen: ResumenDeApertura) -> List[str]:
    """Sugerencias de qué preguntar, derivadas de lo que hay.

    Es la otra mitad de "abrir con contenido": el resumen dice qué pasó y estas
    dicen qué se puede hacer con eso. Sin ellas, el cliente lee el resumen y
    vuelve a quedarse ante el cursor.
    """
    if not resumen.hay_actividad:
        return ["¿Cómo están mis campañas?", "Quiero crear una campaña"]

    sugerencias = []
    if (resumen.por_estado or {}).get(POR_DEBAJO_DEL_BASELINE):
        sugerencias.append("¿Por qué esa campaña anda peor?")
    if (resumen.por_estado or {}).get(VALIDADA):
        sugerencias.append("¿Cuál me está funcionando mejor?")
    sugerencias.append("¿Dónde estoy perdiendo plata?")
    sugerencias.append("Quiero crear una campaña")
    return sugerencias[:4]
