"""Outcome loop del aprendizaje de la IA (Fase 1 del plan de auditoría, C-1+B-3).

Cierra el ciclo que `CampaignLearningLog` dejó abierto: cada campaña creada por
la IA queda con `status="CREATED"` y `outcome_json=NULL` — la promesa era medir
el resultado real tras la ventana de evaluación, y hasta ahora nadie lo hacía.

Todo es DETERMINISTA (regla AI-1, sin LLM): el veredicto sale de agregar los
snapshots reales de la campaña en su ventana de evaluación y compararlos contra
la expectativa que la propia IA usó al proponer el presupuesto (colchón de ~20
conversaciones al menor costo observado → costo esperado = suggested_budget/20).

Veredictos:
- WIN            costo/conv ≤ esperado × 1.2 (el mismo colchón del suggester)
- LOSS           gastó sin ninguna conversación, o costo/conv > esperado × 2
- NEUTRAL        entre medio, o sin expectativa con qué comparar
- NOT_ACTIVATED  la Saga falló o la campaña nunca registró actividad
"""
import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.campaign_snapshot import CampaignSnapshot

logger = logging.getLogger("lejovista.outcome")

# Ventana de evaluación por defecto si el análisis no dejó una (coincide con
# _DEFAULT_EVALUATION_DAYS de campaign_learning.py).
DEFAULT_EVALUATION_DAYS = 7
# Margen sobre el costo esperado dentro del cual la campaña se considera WIN —
# el mismo colchón del 20% que usó el suggester al proponer el presupuesto.
WIN_TOLERANCE = 1.2
# Por encima de este múltiplo del costo esperado, la campaña es LOSS.
LOSS_MULTIPLIER = 2.0


def compute_verdict(
    *,
    spend: float,
    conversations: int,
    expected_cost_per_conv: Optional[float],
) -> Dict[str, Any]:
    """Veredicto determinista a partir de las cifras agregadas de la ventana.

    Puro (sin DB) para poder testearlo como tabla de casos.
    """
    if spend <= 0 and conversations <= 0:
        return {
            "verdict": "NOT_ACTIVATED",
            "cost_per_conv": None,
            "reason": "La campaña no registró gasto ni conversaciones en la ventana de evaluación.",
        }

    if conversations <= 0:
        return {
            "verdict": "LOSS",
            "cost_per_conv": None,
            "reason": f"Gastó S/.{spend:.2f} sin generar ninguna conversación.",
        }

    cost = round(spend / conversations, 2)

    if not expected_cost_per_conv or expected_cost_per_conv <= 0:
        return {
            "verdict": "NEUTRAL",
            "cost_per_conv": cost,
            "reason": (
                f"Logró {conversations} conversación(es) a S/.{cost:.2f} cada una, pero no había "
                "expectativa de costo con qué compararla (cuenta sin histórico al crearla)."
            ),
        }

    if cost <= expected_cost_per_conv * WIN_TOLERANCE:
        verdict, reason = "WIN", (
            f"S/.{cost:.2f} por conversación, dentro del costo esperado "
            f"(~S/.{expected_cost_per_conv:.2f} +20%)."
        )
    elif cost > expected_cost_per_conv * LOSS_MULTIPLIER:
        verdict, reason = "LOSS", (
            f"S/.{cost:.2f} por conversación: más del doble del costo esperado "
            f"(~S/.{expected_cost_per_conv:.2f})."
        )
    else:
        verdict, reason = "NEUTRAL", (
            f"S/.{cost:.2f} por conversación: por encima del costo esperado "
            f"(~S/.{expected_cost_per_conv:.2f}) pero sin llegar al doble."
        )

    return {"verdict": verdict, "cost_per_conv": cost, "reason": reason}


async def evaluate_learning_log(db: AsyncSession, log: CampaignLearningLog) -> Dict[str, Any]:
    """Evalúa UNA entrada del historial: resuelve la campaña real vía el draft,
    agrega sus snapshots en la ventana y persiste el veredicto en `outcome_json`.
    """
    params = log.params_json or {}
    analysis = log.analysis_json or {}
    window_days = int(analysis.get("evaluation_window_days") or DEFAULT_EVALUATION_DAYS)

    # Expectativa que usó la IA: presupuesto = colchón de ~20 conversaciones al
    # menor costo observado → costo esperado por conversación = presupuesto/20.
    suggested_budget = params.get("suggested_budget")
    expected_cost = round(suggested_budget / 20, 2) if suggested_budget and suggested_budget > 0 else None

    outcome: Dict[str, Any] = {
        "evaluation_window_days": window_days,
        "expected_cost_per_conv": expected_cost,
        "hypothesis": analysis.get("summary"),
    }

    # 1. Resolver la campaña real creada por la Saga.
    draft = await db.get(CampaignDraft, log.draft_id) if log.draft_id else None
    campaign_id = None
    if draft:
        campaign_id = (draft.payload or {}).get("result", {}).get("campaign_id")

    if not draft or draft.status == "FAILED" or not campaign_id:
        outcome.update({
            "verdict": "NOT_ACTIVATED",
            "cost_per_conv": None,
            "reason": (
                "La creación de la campaña falló en Meta." if (draft and draft.status == "FAILED")
                else "La campaña nunca llegó a crearse en Meta (sin campaign_id en el draft)."
            ),
        })
        return await _persist_outcome(db, log, outcome)

    outcome["campaign_id"] = campaign_id

    # 2. Agregar snapshots reales de la ventana [creación, creación + N días].
    start = log.created_at.date()
    end = start + datetime.timedelta(days=window_days)
    row = (await db.execute(
        select(
            func.coalesce(func.sum(CampaignSnapshot.spend), 0.0),
            func.coalesce(func.sum(CampaignSnapshot.conversations), 0),
            func.coalesce(func.sum(CampaignSnapshot.conversions), 0),
            func.coalesce(func.sum(CampaignSnapshot.clicks), 0),
            func.coalesce(func.sum(CampaignSnapshot.impressions), 0),
        ).where(
            CampaignSnapshot.ad_account_id == log.ad_account_id,
            CampaignSnapshot.campaign_id == campaign_id,
            CampaignSnapshot.date >= start,
            CampaignSnapshot.date <= end,
        )
    )).one()
    spend, conversations, conversions, clicks, impressions = row

    # Métrica primaria unificada (E-2): conversaciones, con fallback a leads.
    total_conversations = int(conversations or conversions or 0)

    outcome.update({
        "spend": round(float(spend), 2),
        "conversations": total_conversations,
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
    })
    outcome.update(compute_verdict(
        spend=float(spend),
        conversations=total_conversations,
        expected_cost_per_conv=expected_cost,
    ))
    return await _persist_outcome(db, log, outcome)


async def _persist_outcome(db: AsyncSession, log: CampaignLearningLog, outcome: Dict[str, Any]) -> Dict[str, Any]:
    outcome["evaluated_at"] = datetime.datetime.utcnow().isoformat()
    # Columna JSON plana: asignación directa (el valor previo era NULL) — no hay
    # mutación in-place que SQLAlchemy pueda perder (misma lección que A-3).
    log.outcome_json = outcome
    log.outcome_recorded_at = datetime.datetime.utcnow()
    log.status = "EVALUATED"
    db.add(log)
    await db.commit()
    logger.info(
        f"Learning log {log.id} evaluado: {outcome['verdict']} "
        f"(producto '{log.product}', cuenta {log.ad_account_id})"
    )
    return outcome


async def evaluate_pending_outcomes(db: AsyncSession) -> Dict[str, int]:
    """Evalúa todas las entradas CREATED cuya ventana de evaluación ya venció.

    La ventana varía por entrada (la deja el análisis), así que se filtra en
    Python tras un pre-filtro grueso por fecha en SQL.
    """
    now = datetime.datetime.utcnow()
    candidates: List[CampaignLearningLog] = (await db.execute(
        select(CampaignLearningLog).where(
            CampaignLearningLog.status == "CREATED",
            CampaignLearningLog.outcome_json.is_(None),
        ).order_by(CampaignLearningLog.created_at)
    )).scalars().all()

    counts = {"evaluated": 0, "waiting": 0}
    for log in candidates:
        analysis = log.analysis_json or {}
        window_days = int(analysis.get("evaluation_window_days") or DEFAULT_EVALUATION_DAYS)
        if log.created_at + datetime.timedelta(days=window_days) > now:
            counts["waiting"] += 1
            continue
        try:
            await evaluate_learning_log(db, log)
            counts["evaluated"] += 1
        except Exception:
            # Una entrada corrupta no debe frenar la evaluación de las demás.
            logger.exception(f"No se pudo evaluar el learning log {log.id}")
            await db.rollback()

    return counts
