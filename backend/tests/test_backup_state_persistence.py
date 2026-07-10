"""A-3: el backup para rollback debe quedar PERSISTIDO en la base.

`MetaCampaignModifier.modify()` captura el estado previo (backup_state) antes
de escribir en Meta, pero lo asignaba mutando el dict JSON in-place
(`action.parameters["backup_state"] = ...`). SQLAlchemy no detecta mutaciones
internas de columnas JSON sin MutableDict, así que el backup solo existía en
memoria: un rollback en el mismo proceso funcionaba, pero tras un reinicio del
worker (o para auditar la acción después) el backup no estaba en la DB.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.services.meta_ads.modifier import MetaCampaignModifier


@pytest.fixture
async def approved_budget_action(init_db, db_session: AsyncSession):
    org = Organization(slug="bs-org", name="Backup Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="55511", name="Backup Acc", status="ACTIVE",
    )
    acc.set_access_token("mock_access_token")
    db_session.add(acc)
    await db_session.flush()

    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id,
        action_type="MODIFY_BUDGET", risk_level="MEDIUM",
        title="Reducir presupuesto", description="test",
        target_campaign_id="camp_1", target_adset_id="adset_1",
        platform="META",
        parameters={"old_budget": 50.0, "new_budget": 35.0},
        planned_api_calls=[], status="APPROVED",
    )
    db_session.add(action)
    await db_session.commit()
    return acc, action


@pytest.mark.asyncio
async def test_backup_state_survives_a_fresh_read_from_db(
    approved_budget_action, db_session: AsyncSession
):
    acc, action = approved_budget_action
    action_id = action.id

    # SIMULATE_META_WRITES=true en tests: modify() usa el backup simulado y no
    # llama a Meta de verdad, pero el flujo de persistencia es el real.
    await MetaCampaignModifier(db_session).modify(acc, action)

    # Releer DESDE LA BASE (expirar el objeto en memoria): el backup debe estar
    # ahí, no solo en el dict vivo del proceso.
    db_session.expire_all()
    fresh = (await db_session.execute(
        select(AIAction).where(AIAction.id == action_id)
    )).scalar_one()

    assert "backup_state" in fresh.parameters, (
        "backup_state no quedó persistido: la mutación in-place del JSON no fue "
        "detectada por SQLAlchemy"
    )
    assert fresh.parameters["backup_state"].get("daily_budget") == 50.0 * 100
    # Y los parámetros originales no se perdieron al reasignar el dict.
    assert fresh.parameters["new_budget"] == 35.0
