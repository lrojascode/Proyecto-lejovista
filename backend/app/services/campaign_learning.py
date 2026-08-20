"""Razonamiento y convergencia de la IA al crear campañas (Ajuste 2, Fase A).

Dos piezas DETERMINISTAS (sin LLM, sin inventar cifras — regla AI-1):

1. `assess_maturity` — mira el histórico real de la cuenta y decide si ya
   "convergió" a una configuración ganadora estable. Mientras no converja, la IA
   está en modo EXPLORACIÓN (propone ajustes y los deja en evaluación); cuando
   converge, pasa a modo OPTIMIZADO (replica lo que funciona, sin experimentar
   "por buscar").

2. `build_analysis` — arma el objeto que explica, en lenguaje llano, qué está
   haciendo la IA: fortalezas detectadas, debilidades, cambios propuestos (cada
   uno con su porqué), fallos pasados aprendidos, cuántos días de evaluación
   recomienda y su nivel de confianza. Es lo que se muestra al usuario ANTES de
   crear y lo que se persiste en `CampaignLearningLog` como historial de
   entrenamiento.
"""
import statistics
from typing import Any, Dict, List, Optional
from app.utils.formatting import money

# Umbral de coeficiente de variación del costo por conversación por debajo del
# cual se considera que el CPL de la cuenta es "estable".
_STABLE_CV_THRESHOLD = 0.25
# Mínimo de campañas ganadoras (con conversaciones) para hablar de convergencia.
_MIN_WINNERS_FOR_CONVERGENCE = 3
# Fase 1 (1.2): mínimo de WIN propios (campañas creadas por la IA y evaluadas
# como exitosas por el outcome loop) para declarar convergencia. Un CPL estable
# en campañas ajenas no prueba que las decisiones de la IA funcionen.
_MIN_OWN_WINS_FOR_CONVERGENCE = 2
# Días de evaluación por defecto antes de juzgar una campaña nueva.
_DEFAULT_EVALUATION_DAYS = 7


def _own_verdicts(results: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Veredictos de las campañas creadas por la IA, del más reciente al más
    viejo, ignorando NOT_ACTIVATED (una Saga fallida no dice nada del criterio
    de la IA, solo de la infraestructura).

    `results` son pares `{"params": ..., "outcome": ...}` (ver
    `CampaignSuggester._own_evaluated_outcomes`)."""
    return [
        str((r.get("outcome") or {}).get("verdict", "")).upper()
        for r in (results or [])
        if r and str((r.get("outcome") or {}).get("verdict", "")).upper() in ("WIN", "LOSS", "NEUTRAL")
    ]


def own_measured_costs(results: Optional[List[Dict[str, Any]]]) -> List[float]:
    """Costos por conversación REALES de las campañas propias ya evaluadas.

    Incluye WIN, NEUTRAL y LOSS: quedarse solo con las WIN reintroduciría sesgo
    de supervivencia y volvería a dar una expectativa optimista. Un LOSS con 0
    conversaciones no aporta costo medible (`cost_per_conv` None) y se descarta.
    """
    costs = []
    for r in (results or []):
        cost = (r.get("outcome") or {}).get("cost_per_conv")
        if isinstance(cost, (int, float)) and cost > 0:
            costs.append(float(cost))
    return costs


def calibrated_cost_per_conv(results: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    """Mediana del costo por conversación realmente logrado por la IA en esta
    cuenta, o None si aún no hay ninguno medible.

    Es la base para el presupuesto (R1): la mejor estimación de lo que costará
    una campaña es lo que costaron las anteriores — no `min(costs)`, el récord
    histórico de la cuenta, que es optimista por construcción y ponía la vara
    de la evaluación en el mejor caso (haciendo la convergencia casi
    inalcanzable). Mediana, no media, por robustez con 2-3 muestras.
    """
    costs = own_measured_costs(results)
    return round(statistics.median(costs), 2) if costs else None


def latest_winning_params(results: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """`params_json` del WIN propio más reciente (R2): la configuración que tiene
    un resultado medido que la respalda. `results` viene del más reciente al más
    viejo."""
    for r in (results or []):
        if str((r.get("outcome") or {}).get("verdict", "")).upper() == "WIN":
            return r.get("params") or None
    return None


def assess_maturity(
    campaigns: List[Dict[str, Any]],
    evaluated_outcomes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Decide el modo de la IA a partir del histórico de campañas de la cuenta
    Y de los resultados reales de sus propias creaciones (outcome loop, Fase 1).

    `campaigns` es la lista de `ReportBuilder` (cada una con `conversations` y
    `cost_per_conv`). `evaluated_outcomes` son los `outcome_json` de los
    `CampaignLearningLog` EVALUATED de la cuenta, del más reciente al más
    viejo. Devuelve {converged, mode, reason}.
    """
    verdicts = _own_verdicts(evaluated_outcomes)

    # Los resultados propios mandan sobre el CV: si las últimas 2 creaciones de
    # la IA no funcionaron, se vuelve a explorar aunque el CPL histórico de la
    # cuenta (que puede venir de campañas creadas a mano) luzca estable.
    if len(verdicts) >= 2 and verdicts[0] == "LOSS" and verdicts[1] == "LOSS":
        return {
            "converged": False,
            "mode": "exploration",
            "reason": (
                "Las últimas 2 campañas creadas por la IA no rindieron (resultado: no funcionó). "
                "Se descarta esa configuración y se vuelve a explorar."
            ),
        }

    winners = [c for c in campaigns if c.get("conversations", 0) > 0]

    if len(winners) < _MIN_WINNERS_FOR_CONVERGENCE:
        return {
            "converged": False,
            "mode": "exploration",
            "reason": (
                f"Solo hay {len(winners)} campaña(s) con conversaciones; se necesitan al menos "
                f"{_MIN_WINNERS_FOR_CONVERGENCE} para confiar en un patrón. La IA sigue explorando."
            ),
        }

    costs = [c["cost_per_conv"] for c in winners]
    mean = sum(costs) / len(costs)
    cv = (statistics.pstdev(costs) / mean) if mean > 0 else 1.0

    if cv <= _STABLE_CV_THRESHOLD:
        # CPL estable, pero la convergencia exige además evidencia PROPIA: al
        # menos 2 campañas creadas por la IA con resultado WIN verificado.
        own_wins = verdicts.count("WIN")
        if own_wins < _MIN_OWN_WINS_FOR_CONVERGENCE:
            return {
                "converged": False,
                "mode": "exploration",
                "reason": (
                    f"El costo por conversación de la cuenta es estable (variación {cv * 100:.0f}%), "
                    f"pero la IA solo tiene {own_wins} campaña(s) propia(s) con resultado verificado "
                    f"como exitoso; necesita {_MIN_OWN_WINS_FOR_CONVERGENCE} para confiar en su propia "
                    "configuración. Sigue explorando con lo aprendido."
                ),
            }
        return {
            "converged": True,
            "mode": "optimized",
            "reason": (
                f"{len(winners)} campañas ganadoras con costo por conversación estable "
                f"(variación {cv * 100:.0f}%) y {own_wins} creaciones propias de la IA verificadas "
                "como exitosas. La IA replica la configuración ganadora sin experimentar."
            ),
        }

    return {
        "converged": False,
        "mode": "exploration",
        "reason": (
            f"El costo por conversación todavía varía {cv * 100:.0f}% entre {len(winners)} campañas; "
            "la IA sigue ajustando para estabilizarlo."
        ),
    }


def _own_failure_notes(results: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Fallos PROPIOS de la IA (LOSS ya evaluados), con su configuración y sus
    cifras reales.

    Antes `past_failures` solo miraba campañas de la cuenta con gasto y 0
    conversiones, así que los fracasos de la propia IA no aparecían por ningún
    lado. A propósito NO se culpa a un parámetro concreto: un LOSS no dice
    *cuál* falló, y castigar "la edad 45-54" por un mal resultado sería inventar
    causalidad sobre 2-3 muestras. La corrección real la hacen R1 (recalibrar el
    costo esperado) y R2 (solo se replica lo que ganó); esto aporta la
    transparencia, que es lo honesto que se puede extraer.
    """
    notes: List[str] = []
    for r in (results or []):
        outcome = r.get("outcome") or {}
        if str(outcome.get("verdict", "")).upper() != "LOSS":
            continue
        params = r.get("params") or {}
        config_bits = []
        if params.get("best_age_range"):
            config_bits.append(f"edad {params['best_age_range']}")
        if params.get("best_device"):
            config_bits.append(f"dispositivo {params['best_device']}")
        if params.get("budget"):
            config_bits.append(f"presupuesto {money(params['budget'])}")
        config = ", ".join(config_bits) if config_bits else "configuración registrada"
        notes.append(
            f"Una campaña propia con {config} no rindió: {outcome.get('reason', 'sin detalle')} "
            "— no se replica esa configuración."
        )
    return notes


def build_analysis(
    report: Dict[str, Any],
    *,
    best: Optional[Dict[str, Any]],
    excluded_placements: List[str],
    best_device: Optional[str],
    best_age_range: Optional[str],
    suggested_budget: float,
    maturity: Dict[str, Any],
    evaluated_outcomes: Optional[List[Dict[str, Any]]] = None,
    calibrated_cost: Optional[float] = None,
    replicated_from_win: bool = False,
) -> Dict[str, Any]:
    """Construye el objeto de razonamiento explicable de la IA.

    `calibrated_cost` / `replicated_from_win` reflejan lo que los resultados
    reales cambiaron en esta propuesta (R1/R2), para que la explicación diga lo
    que de verdad se hizo y no una promesa genérica."""
    campaigns = report.get("campaigns", [])
    window = report.get("window_days", _DEFAULT_EVALUATION_DAYS)

    strengths: List[str] = []
    weaknesses: List[str] = []
    changes: List[Dict[str, str]] = []
    past_failures: List[str] = []

    # Fase 1 (1.2): track record propio — qué pasó de verdad con las campañas
    # que la IA ya creó y el outcome loop evaluó.
    verdicts = _own_verdicts(evaluated_outcomes)
    own_wins = verdicts.count("WIN")
    own_losses = verdicts.count("LOSS")
    track_record = None
    if verdicts:
        track_record = (
            f"De las {len(verdicts)} campañas que la IA creó y ya evaluó, "
            f"{own_wins} funcionaron, {own_losses} no funcionaron y "
            f"{len(verdicts) - own_wins - own_losses} quedaron neutrales."
        )
        if own_wins:
            strengths.append(track_record)
        else:
            weaknesses.append(track_record)

    if best:
        strengths.append(
            f"Campaña más eficiente '{best['name']}': {money(best['cost_per_conv'])} por "
            f"conversación, CTR {best['ctr']:.2f}%."
        )
    if best_device:
        strengths.append(f"El dispositivo '{best_device}' es el de menor costo por conversación.")
    if best_age_range:
        strengths.append(f"El rango de edad '{best_age_range}' es el de menor costo por conversación.")

    # R3: los fracasos PROPIOS de la IA van primero — son los que de verdad
    # informan su criterio (los de la cuenta pueden venir de campañas ajenas).
    past_failures.extend(_own_failure_notes(evaluated_outcomes))

    for c in campaigns:
        if c.get("spend", 0) > 0 and c.get("conversations", 0) == 0:
            past_failures.append(
                f"'{c['name']}' gastó {money(c['spend'])} sin ninguna conversación en los últimos "
                f"{window} días — se evita repetir su configuración."
            )

    if excluded_placements:
        weaknesses.append(
            "Placements con gasto y 0 conversiones: "
            + ", ".join(p.replace("_", " ") for p in excluded_placements) + "."
        )
        changes.append({
            "change": "Excluir los placements sin retorno",
            "why": "Concentra el presupuesto donde sí se generan conversaciones.",
        })
    if best_device:
        changes.append({
            "change": f"Priorizar el dispositivo {best_device}",
            "why": "Es el que ha mostrado el menor costo por conversación.",
        })
    if best_age_range:
        changes.append({
            "change": f"Enfocar el rango de edad {best_age_range}",
            "why": "Es el segmento con menor costo por conversación observado.",
        })
    if calibrated_cost:
        why_budget = (
            f"Colchón de ~20 conversaciones a {money(calibrated_cost)} — el costo real que la IA "
            "ha logrado en esta cuenta, no el mejor caso histórico."
        )
    elif best:
        why_budget = (
            "Colchón de ~20 conversaciones al menor costo observado. Aún no hay resultados propios "
            "medidos con qué calibrarlo."
        )
    else:
        why_budget = "Valor por defecto conservador: aún no hay histórico con qué calibrar."
    changes.append({
        "change": f"Presupuesto de partida {money(suggested_budget)}",
        "why": why_budget,
    })

    if not past_failures:
        past_failures.append("Sin fallos relevantes en la ventana analizada.")
    if not weaknesses:
        weaknesses.append("No se detectó desperdicio de placements en la ventana analizada.")

    winners = [c for c in campaigns if c.get("conversations", 0) > 0]
    confidence = "alta" if len(winners) >= _MIN_WINNERS_FOR_CONVERGENCE else ("media" if winners else "baja")

    if maturity.get("converged") and replicated_from_win:
        summary = (
            "La cuenta ya convergió: replico la configuración exacta (dispositivo, edad y "
            "placements) de mi última campaña con resultado verificado como exitoso, sin "
            "experimentar."
        )
    elif maturity.get("converged"):
        summary = (
            "La cuenta ya convergió a una configuración ganadora estable: creo la campaña "
            "optimizada replicando lo que funciona, sin experimentar."
        )
    elif best:
        summary = (
            "Hay histórico ganador: parto de esa configuración y ajusto placements y segmentación "
            "para bajar el costo por conversación. La dejo en evaluación para confirmar."
        )
    else:
        summary = (
            "Aún no hay histórico con conversiones: creo una campaña base conservadora y la dejo "
            "en evaluación para aprender de sus resultados."
        )

    if track_record:
        summary = f"{summary} {track_record}"

    return {
        "summary": summary,
        "mode": maturity.get("mode"),
        "track_record": track_record,
        "converged": maturity.get("converged", False),
        "maturity_reason": maturity.get("reason"),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "changes_proposed": changes,
        "past_failures": past_failures,
        "evaluation_window_days": _DEFAULT_EVALUATION_DAYS,
        "confidence": confidence,
    }
