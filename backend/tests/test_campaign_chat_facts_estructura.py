"""Bloque `estructura` del chat (punto 1 y 2 del plan de arreglos): objetivo,
estado y fechas de campaña resueltos desde `CampaignSnapshot`, y un "no sé"
honesto —sin relleno de `account`— cuando esos datos todavía no se sincronizan.
"""
import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.campaign_chat.facts import resolve_facts


@pytest.fixture
async def org_and_account(init_db, db_session: AsyncSession):
    org = Organization(
        slug="estructura-org", name="Estructura Org",
        ai_model="google/gemini-2.5-flash", max_budget=2000.0, status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()

    acc = AdAccount(
        organization_id=org.id, platform="META", external_account_id="77665",
        name="Estructura Account", status="ACTIVE",
        spend_cap=5000.0, amount_spent=2399.97,
    )
    acc.set_access_token("mock_access_token")
    db_session.add(acc)
    await db_session.flush()
    await db_session.commit()
    return org, acc


async def _add_snapshot(db_session, acc, org, **kwargs):
    defaults = dict(
        ad_account_id=acc.id, organization_id=org.id,
        campaign_id="camp_1", campaign_name="Camp Uno", status="ACTIVE",
        budget=50.0, spend=50.0, date=datetime.date.today(),
    )
    defaults.update(kwargs)
    db_session.add(CampaignSnapshot(**defaults))
    await db_session.commit()


@pytest.mark.asyncio
async def test_schedule_topic_reports_real_dates_from_meta(org_and_account, db_session: AsyncSession):
    org, acc = org_and_account
    created = datetime.datetime(2026, 6, 20, 10, 30)
    await _add_snapshot(
        db_session, acc, org,
        objective="OUTCOME_ENGAGEMENT", created_time=created, start_time=created, stop_time=None,
    )

    facts, canonical = await resolve_facts(db_session, org.id, acc, "SCHEDULE")
    assert facts["campanas_estructura"][0]["created_time"] == "2026-06-20"
    assert "Camp Uno" in canonical
    assert "2026-06-20" in canonical
    assert "sigue activa" in canonical
    # Regla del punto 2: una pregunta de fechas nunca debe traer el tope de gasto.
    assert "tope de gasto" not in canonical
    assert "5.000" not in canonical


@pytest.mark.asyncio
async def test_objective_topic_reports_real_objective_not_inferred_from_name(org_and_account, db_session: AsyncSession):
    org, acc = org_and_account
    await _add_snapshot(
        db_session, acc, org,
        campaign_name="MTV_MSJ_CABLE_GPT_CERCADO_JUN2026", objective="OUTCOME_LEADS",
    )

    facts, canonical = await resolve_facts(db_session, org.id, acc, "OBJECTIVE")
    assert facts["campanas_estructura"][0]["objective"] == "OUTCOME_LEADS"
    assert "Generación de leads" in canonical
    assert "tope de gasto" not in canonical


@pytest.mark.asyncio
async def test_schedule_question_without_synced_dates_is_honest_and_offers_what_it_has(
    org_and_account, db_session: AsyncSession,
):
    """Simula una campaña sincronizada ANTES de este arreglo: kpis/ranking
    existen pero created_time/start_time/stop_time siguen NULL. La respuesta
    debe decir qué falta y ofrecer lo que sí hay, sin mencionar el tope de gasto."""
    org, acc = org_and_account
    await _add_snapshot(
        db_session, acc, org,
        objective=None, created_time=None, start_time=None, stop_time=None,
    )

    facts, canonical = await resolve_facts(db_session, org.id, acc, "SCHEDULE")
    assert "todavía" in canonical.lower() or "por ahora" in canonical.lower()
    assert "1 campañas" in canonical or "1 campaña" in canonical.lower() or "(1 activas)" in canonical
    assert "tope de gasto" not in canonical
    assert "5.000" not in canonical
    assert "2.399" not in canonical


@pytest.mark.asyncio
async def test_objective_question_without_synced_objective_is_honest(org_and_account, db_session: AsyncSession):
    org, acc = org_and_account
    await _add_snapshot(db_session, acc, org, objective=None)

    facts, canonical = await resolve_facts(db_session, org.id, acc, "OBJECTIVE")
    assert "objetivo" in canonical.lower()
    assert "tope de gasto" not in canonical
