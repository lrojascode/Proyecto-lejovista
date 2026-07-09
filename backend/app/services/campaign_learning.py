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

# Umbral de coeficiente de variación del costo por conversación por debajo del
# cual se considera que el CPL de la cuenta es "estable".
_STABLE_CV_THRESHOLD = 0.25
# Mínimo de campañas ganadoras (con conversaciones) para hablar de convergencia.
_MIN_WINNERS_FOR_CONVERGENCE = 3
# Días de evaluación por defecto antes de juzgar una campaña nueva.
_DEFAULT_EVALUATION_DAYS = 7


def assess_maturity(campaigns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Decide el modo de la IA a partir del histórico de campañas de la cuenta.

    `campaigns` es la lista de `ReportBuilder` (cada una con `conversations` y
    `cost_per_conv`). Devuelve {converged, mode, reason}.
    """
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
        return {
            "converged": True,
            "mode": "optimized",
            "reason": (
                f"{len(winners)} campañas ganadoras con costo por conversación estable "
                f"(variación {cv * 100:.0f}%). La IA replica la configuración ganadora sin experimentar."
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


def build_analysis(
    report: Dict[str, Any],
    *,
    best: Optional[Dict[str, Any]],
    excluded_placements: List[str],
    best_device: Optional[str],
    best_age_range: Optional[str],
    suggested_budget: float,
    maturity: Dict[str, Any],
) -> Dict[str, Any]:
    """Construye el objeto de razonamiento explicable de la IA."""
    campaigns = report.get("campaigns", [])
    window = report.get("window_days", _DEFAULT_EVALUATION_DAYS)

    strengths: List[str] = []
    weaknesses: List[str] = []
    changes: List[Dict[str, str]] = []
    past_failures: List[str] = []

    if best:
        strengths.append(
            f"Campaña más eficiente '{best['name']}': S/.{best['cost_per_conv']:.2f} por "
            f"conversación, CTR {best['ctr']:.2f}%."
        )
    if best_device:
        strengths.append(f"El dispositivo '{best_device}' es el de menor costo por conversación.")
    if best_age_range:
        strengths.append(f"El rango de edad '{best_age_range}' es el de menor costo por conversación.")

    for c in campaigns:
        if c.get("spend", 0) > 0 and c.get("conversations", 0) == 0:
            past_failures.append(
                f"'{c['name']}' gastó S/.{c['spend']:.2f} sin ninguna conversación en los últimos "
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
    changes.append({
        "change": f"Presupuesto de partida S/.{suggested_budget:.0f}",
        "why": (
            "Colchón de ~20 conversaciones al menor costo observado."
            if best else "Valor por defecto conservador: aún no hay histórico con qué calibrar."
        ),
    })

    if not past_failures:
        past_failures.append("Sin fallos relevantes en la ventana analizada.")
    if not weaknesses:
        weaknesses.append("No se detectó desperdicio de placements en la ventana analizada.")

    winners = [c for c in campaigns if c.get("conversations", 0) > 0]
    confidence = "alta" if len(winners) >= _MIN_WINNERS_FOR_CONVERGENCE else ("media" if winners else "baja")

    if maturity.get("converged"):
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

    return {
        "summary": summary,
        "mode": maturity.get("mode"),
        "converged": maturity.get("converged", False),
        "maturity_reason": maturity.get("reason"),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "changes_proposed": changes,
        "past_failures": past_failures,
        "evaluation_window_days": _DEFAULT_EVALUATION_DAYS,
        "confidence": confidence,
    }
