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
from app.models.proposal_evidence import ProposalEvidence
from app.utils.formatting import money

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
            "reason": f"Gastó {money(spend)} sin generar ninguna conversación.",
        }

    cost = round(spend / conversations, 2)

    if not expected_cost_per_conv or expected_cost_per_conv <= 0:
        return {
            "verdict": "NEUTRAL",
            "cost_per_conv": cost,
            "reason": (
                f"Logró {conversations} conversación(es) a {money(cost)} cada una, pero no había "
                "expectativa de costo con qué compararla (cuenta sin histórico al crearla)."
            ),
        }

    if cost <= expected_cost_per_conv * WIN_TOLERANCE:
        verdict, reason = "WIN", (
            f"{money(cost)} por conversación, dentro del costo esperado "
            f"(~{money(expected_cost_per_conv)} +20%)."
        )
    elif cost > expected_cost_per_conv * LOSS_MULTIPLIER:
        verdict, reason = "LOSS", (
            f"{money(cost)} por conversación: más del doble del costo esperado "
            f"(~{money(expected_cost_per_conv)})."
        )
    else:
        verdict, reason = "NEUTRAL", (
            f"{money(cost)} por conversación: por encima del costo esperado "
            f"(~{money(expected_cost_per_conv)}) pero sin llegar al doble."
        )

    return {"verdict": verdict, "cost_per_conv": cost, "reason": reason}


async def evaluate_learning_log(db: AsyncSession, log: CampaignLearningLog) -> Dict[str, Any]:
    """Evalúa UNA entrada del historial: resuelve la campaña real vía el draft,
    agrega sus snapshots en la ventana y persiste el veredicto en `outcome_json`.
    """
    params = log.params_json or {}
    analysis = log.analysis_json or {}
    window_days = int(analysis.get("evaluation_window_days") or DEFAULT_EVALUATION_DAYS)

    # El baseline CONGELADO en el momento de proponer (spec 11, criterio 13).
    #
    # El veredicto tiene que compararse contra la referencia que estaba vigente
    # cuando se tomó la decisión, no contra la de hoy. El baseline se recalibra
    # —entran campañas nuevas, salen las viejas de la ventana— y juzgar una
    # campaña de hace dos meses contra la referencia actual la premia o la
    # castiga por lo que hicieron OTRAS campañas después. Eso no es medir el
    # resultado de una decisión, es medir el ruido posterior.
    congelado = await _baseline_congelado(db, log)
    expected_cost = congelado.get("mediana") if congelado else None
    origen_expectativa = "baseline_congelado" if expected_cost else None

    # Sin baseline congelado se cae a la expectativa que usó el suggester:
    # presupuesto = colchón de ~20 conversaciones → costo esperado =
    # presupuesto/20. Es lo que había antes de esta spec, y sigue siendo lo
    # correcto para las propuestas anteriores a ella.
    if not expected_cost:
        suggested_budget = params.get("suggested_budget")
        if suggested_budget and suggested_budget > 0:
            expected_cost = round(suggested_budget / 20, 2)
            origen_expectativa = "presupuesto_sugerido"

    outcome: Dict[str, Any] = {
        "evaluation_window_days": window_days,
        "expected_cost_per_conv": expected_cost,
        # De dónde salió la expectativa. Sin esto, dos veredictos calculados
        # contra referencias distintas se leen como si fueran comparables.
        "expectativa_origen": origen_expectativa,
        "baseline_congelado": congelado,
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


async def _baseline_congelado(
    db: AsyncSession, log: CampaignLearningLog
) -> Optional[Dict[str, Any]]:
    """El baseline que se fijó al proponer esta campaña, si lo hay.

    Se busca por `draft_id`, que es lo que une el registro de aprendizaje con la
    trazabilidad de la propuesta. Devuelve `None` para las campañas anteriores a
    la spec 11: no tienen evidencia persistida y no se puede reconstruir, porque
    reconstruirla hoy daría el baseline de hoy, que es exactamente lo que este
    mecanismo evita.
    """
    if not log.draft_id:
        return None
    fila = (await db.execute(
        select(ProposalEvidence)
        .where(ProposalEvidence.draft_id == log.draft_id)
        .order_by(ProposalEvidence.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if fila is None or not fila.baseline_json:
        return None
    return dict(fila.baseline_json)


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
