"""B-1: cada análisis reemplaza la foto anterior de insights de la cuenta.

Sin el dedupe, el análisis corre encadenado tras cada sync (más el trigger
manual y el Beat diario) y la misma alerta ("CPL crítico", "saturación"...)
se acumulaba repetida en /diagnostics hasta descartarla a mano.
"""
import datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.services.ai.engine import AIEngine


@pytest.fixture
async def org_with_failing_campaign(init_db, db_session: AsyncSession):
    org = Organization(
        slug="dedupe-org", name="Dedupe Org",
        ai_model="google/gemini-2.5-flash", max_budget=2000.0, status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()

    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="99887", name="Dedupe Account", status="ACTIVE",
    )
    acc.set_access_token("mock_access_token")
    db_session.add(acc)
    await db_session.flush()

    # Campaña con gasto e impresiones y 0 conversiones -> dispara la Regla D
    # (Capa 1) en cada análisis, de forma determinista.
    today = datetime.date.today()
    for i in range(7):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id,
            campaign_id="camp_zero_conv", campaign_name="Zero Conv Campaign",
            status="ACTIVE", budget=50.0, spend=50.0,
            impressions=2000, clicks=30, conversions=0,
            cpl=0.0, ctr=1.5, frequency=1.2,
            placements_breakdown={}, demographics_breakdown={},
            date=today - datetime.timedelta(days=i),
        ))
    await db_session.commit()
    return org.id, acc.id


async def _active_insights(db: AsyncSession, acc_id: str):
    q = select(AIInsight).where(
        AIInsight.ad_account_id == acc_id,
        AIInsight.is_dismissed == False,  # noqa: E712
    )
    return (await db.execute(q)).scalars().all()


@pytest.mark.asyncio
async def test_second_analysis_does_not_duplicate_active_insights(
    org_with_failing_campaign, db_session: AsyncSession
):
    org_id, acc_id = org_with_failing_campaign
    engine = AIEngine(db_session)

    await engine.run_analysis(org_id)
    first = await _active_insights(db_session, acc_id)
    assert len(first) > 0, "el fixture debe disparar al menos un insight"

    await engine.run_analysis(org_id)
    second = await _active_insights(db_session, acc_id)

    # La cantidad de alertas ACTIVAS no crece con cada corrida: se reemplazan.
    assert len(second) == len(first)
    # Y las activas son las de la última corrida (las viejas quedaron descartadas).
    assert {i.id for i in second}.isdisjoint({i.id for i in first})


@pytest.mark.asyncio
async def test_stale_insights_clear_when_problem_resolves(
    org_with_failing_campaign, db_session: AsyncSession
):
    """Si el problema desaparece del histórico, la alerta vieja no queda viva."""
    org_id, acc_id = org_with_failing_campaign
    engine = AIEngine(db_session)

    await engine.run_analysis(org_id)
    assert len(await _active_insights(db_session, acc_id)) > 0

    # "Se arregla" la campaña: ahora convierte (borra la condición de la Regla D
    # actualizando los snapshots existentes).
    from sqlalchemy import update
    await db_session.execute(
        update(CampaignSnapshot)
        .where(CampaignSnapshot.campaign_id == "camp_zero_conv")
        .values(conversions=20, cpl=2.5)
    )
    await db_session.commit()

    await engine.run_analysis(org_id)
    remaining = await _active_insights(db_session, acc_id)
    # Sin condición de alerta, no debe quedar viva la alerta de la corrida anterior.
    titles = [i.title for i in remaining]
    assert "Conversiones en cero" not in titles
