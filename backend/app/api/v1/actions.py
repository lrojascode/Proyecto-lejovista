from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.ai_action import AIAction
from app.schemas.schemas import AIActionResponse, ActionApprove
from app.services.budget_gate import (
    BudgetLimitExceeded, MixedCurrencies, assert_cabe_en_el_mes,
)
from app.utils.security import (
    get_current_user, get_active_ad_account_id, require_role, require_verified_session,
)
from app.workers.tasks import execute_action_task
import datetime
from typing import List, Optional

router = APIRouter(prefix="/actions", tags=["actions"])

@router.get("", response_model=List[AIActionResponse])
async def list_pending_actions(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna la lista de acciones propuestas por la IA que están pendientes de
    aprobación, para la cuenta de anuncios activa.
    """
    conditions = [
        AIAction.organization_id == current_user.organization_id,
        AIAction.status == "PROPOSED",
    ]
    if ad_account_id is not None:
        conditions.append(AIAction.ad_account_id == ad_account_id)
    q = select(AIAction).where(*conditions).order_by(AIAction.proposed_at.desc())

    res = await db.execute(q)
    return res.scalars().all()

@router.get("/history", response_model=List[AIActionResponse])
async def list_action_history(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna el historial de acciones procesadas (aprobadas, rechazadas, ejecutadas),
    para la cuenta de anuncios activa.
    """
    conditions = [
        AIAction.organization_id == current_user.organization_id,
        AIAction.status != "PROPOSED",
    ]
    if ad_account_id is not None:
        conditions.append(AIAction.ad_account_id == ad_account_id)
    q = select(AIAction).where(*conditions).order_by(AIAction.proposed_at.desc())

    res = await db.execute(q)
    return res.scalars().all()

# Acciones cuya aprobación compromete gasto nuevo. Pausar o reanudar no lo hace
# —una campaña reanudada gasta contra el presupuesto que ya tenía—, así que
# pasarlas por la compuerta bloquearía la contención de gasto justo cuando el
# límite está agotado, que es cuando más falta hace poder pausar.
ACCIONES_QUE_COMPROMETEN_GASTO = {"MODIFY_BUDGET", "CREATE_CAMPAIGN"}


def _monto_que_compromete(action: AIAction) -> Optional[float]:
    """Cuánto margen del mes consume aprobar esta acción, o None si no consume.

    Para un cambio de presupuesto es el INCREMENTO, no el monto nuevo: lo
    anterior ya está contado en el gasto real o en el compromiso del Draft.
    Contarlo entero haría que subir de 100 a 110 consumiera 110 de margen.
    """
    if action.action_type not in ACCIONES_QUE_COMPROMETEN_GASTO:
        return None
    params = action.parameters or {}
    if action.action_type == "MODIFY_BUDGET":
        nuevo = params.get("new_budget")
        if nuevo is None:
            return None
        incremento = float(nuevo) - float(params.get("old_budget") or 0)
        # Bajar un presupuesto nunca se rechaza por límite.
        return incremento if incremento > 0 else None
    return params.get("budget") or params.get("suggested_budget")


@router.post("/{action_id}/approve", response_model=AIActionResponse)
async def approve_or_reject_action(
    action_id: str,
    payload: ActionApprove,
    current_user: User = Depends(require_role("OPERATOR")),
    # Aprobar autoriza una escritura real en Meta: si no se puede comprobar que
    # esta sesión siga siendo válida, la autorización no se concede (spec 01).
    _verificada: User = Depends(require_verified_session),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Aprueba o rechaza una acción sugerida por la IA.
    Si se aprueba, se encola la tarea de ejecución en Celery de inmediato.
    """
    action = await db.get(AIAction, action_id)
    if not action or action.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Acción propuesta no encontrada.")
    if ad_account_id is not None and action.ad_account_id != ad_account_id:
        raise HTTPException(status_code=404, detail="Acción propuesta no encontrada.")
        
    if action.status != "PROPOSED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La acción no se puede procesar porque ya está en estado '{action.status}'."
        )

    if payload.approve:
        # Compuerta de presupuesto mensual (spec 02). Aprobar es una de las tres
        # superficies que comprometen gasto: una acción propuesta cuando había
        # margen puede aprobarse cuando ya no lo hay.
        monto = _monto_que_compromete(action)
        if monto is not None:
            try:
                await assert_cabe_en_el_mes(db, current_user.organization_id, monto)
            except BudgetLimitExceeded as e:
                raise HTTPException(status_code=400, detail=e.as_detail())
            except MixedCurrencies as e:
                raise HTTPException(status_code=400, detail=e.as_detail())

        action.status = "APPROVED"
        action.approved_at = datetime.datetime.utcnow()
        action.approved_by = current_user.id
        db.add(action)
        await db.commit()
        
        # Encolar ejecución asíncrona
        execute_action_task.delay(action.id)
    else:
        action.status = "REJECTED"
        action.rejected_at = datetime.datetime.utcnow()
        action.reject_reason = payload.reject_reason or "Rechazado por el usuario"
        db.add(action)
        await db.commit()
        
    return action

@router.get("/{action_id}/status", response_model=AIActionResponse)
async def get_action_status(
    action_id: str,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna el estado de ejecución y resultado de una acción.
    """
    action = await db.get(AIAction, action_id)
    if not action or action.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Acción no encontrada.")
    if ad_account_id is not None and action.ad_account_id != ad_account_id:
        raise HTTPException(status_code=404, detail="Acción no encontrada.")

    return action
