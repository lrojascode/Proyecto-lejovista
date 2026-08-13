"""Máquina de estados del chat de creación de campañas (CH-1). Pura: no toca
la BD ni hace llamadas HTTP — el motor (`engine.py`) es quien tiene el
contexto para ejecutar los pasos automáticos (armar la propuesta, correr el
preflight) y persistir el resultado.

`next_state` es una función del CONJUNTO de slots llenos, no un contador
lineal: si el usuario contesta dos cosas en un mismo mensaje, se saltan dos
estados gratis. Dos compuertas (`REVIEW_PLAN`, `CONFIRM_RISK`) son las únicas
que exigen una decisión explícita del usuario antes de seguir — ninguna otra
transición depende de un clic.
"""
from dataclasses import dataclass
from typing import FrozenSet, Optional

from app.services.campaign_chat.slots import HARD_INVALIDATING_SLOTS, SOFT_INVALIDATING_SLOTS

# Estados "ask": el motor espera un mensaje del usuario.
ASK_STATES = frozenset({"ASK_GOAL", "ASK_MEDIA"})
# Estados "gate": el motor espera una DECISIÓN explícita (aceptar/cambiar/rechazar).
GATE_STATES = frozenset({"REVIEW_PLAN", "CONFIRM_RISK"})
# Estados terminales: la sesión ya no avanza sola.
TERMINAL_STATES = frozenset({"DONE", "FAILED", "ABANDONED", "BLOCKED_CAP"})


@dataclass(frozen=True)
class SessionFacts:
    """Los hechos mínimos que `next_state` necesita — no el objeto ORM
    completo, para que la función siga siendo pura y fácil de testear."""

    goal_text: Optional[str]
    image_path: Optional[str]
    video_path: Optional[str]
    has_proposal: bool
    proposal_accepted: bool
    draft_id: Optional[str]


def next_state(facts: SessionFacts) -> str:
    if facts.draft_id:
        return "CREATING"
    if not facts.goal_text:
        return "ASK_GOAL"
    if not (facts.image_path or facts.video_path):
        return "ASK_MEDIA"
    if not facts.has_proposal:
        return "BUILD_PLAN"
    if not facts.proposal_accepted:
        return "REVIEW_PLAN"
    return "CONFIRM_RISK"


@dataclass(frozen=True)
class Invalidation:
    """Qué resetear en la sesión tras aplicar un cambio de slots."""

    clear_proposal: bool = False
    clear_acceptance: bool = False
    clear_preflight: bool = False


def invalidation_for_changed_slots(changed_keys: FrozenSet[str]) -> Invalidation:
    """Matriz de invalidación: cambiar el objetivo/la foto obliga a rehacer la
    propuesta entera (copy incluido); cambiar solo presupuesto/días/destino
    mantiene la propuesta pero exige volver a aceptar y re-validar el riesgo.
    Sin esto, "subí el presupuesto" podría terminar creando la campaña con el
    presupuesto viejo si el usuario ya había aceptado antes."""
    if changed_keys & HARD_INVALIDATING_SLOTS:
        return Invalidation(clear_proposal=True, clear_acceptance=True, clear_preflight=True)
    if changed_keys & SOFT_INVALIDATING_SLOTS:
        return Invalidation(clear_proposal=False, clear_acceptance=True, clear_preflight=True)
    return Invalidation()
