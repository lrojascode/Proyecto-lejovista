"""C-3: AIEngine._build_brief_context arma el contexto enriquecido del Master
Brief v2 (target_cpl, comparativa 7d vs 7d previos, top/bottom, pacing) a
partir de ReportBuilder — no recalcula breakdowns propios.
"""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.services.ai.engine import AIEngine


async def _make_account(db: AsyncSession, slug="engine-brief-org"):
    org = Organization(slug=slug, name="Engine Brief Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Engine Brief Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_brief_context_computes_deltas_and_pacing(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session)
    today = datetime.date.today()

    # Ventana actual (últimos 7 días): 100 de gasto, 20 conversiones.
    for i in range(7):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id="camp_1",
            campaign_name="Campaña Activa", status="ACTIVE", budget=10.0,
            spend=100.0 / 7, conversions=20 // 7 or 1, impressions=500, clicks=20,
            date=today - datetime.timedelta(days=i),
        ))
    # Ventana previa (días 8-14): 80 de gasto, 25 conversiones -> más barata antes.
    for i in range(7, 14):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id="camp_1",
            campaign_name="Campaña Activa", status="ACTIVE", budget=10.0,
            spend=80.0 / 7, conversions=25 // 7 or 1, impressions=500, clicks=20,
            date=today - datetime.timedelta(days=i),
        ))
    await db_session.commit()

    engine = AIEngine(db_session)
    ctx = await engine._build_brief_context(acc)

    assert ctx["currency"] == "PEN"
    assert ctx["target_cpl"] > 0
    assert ctx["kpis_7d"]["spend"] > 0
    assert ctx["kpis_prev_7d"]["spend"] > 0
    # Gastó más en la ventana actual que en la previa -> delta positivo.
    assert ctx["deltas_pct"]["spend"] > 0
    assert ctx["pacing"]["daily_budget_total"] >= 0
    assert ctx["pacing"]["daily_spend_avg"] == round(ctx["kpis_7d"]["spend"] / 7, 2)
    assert isinstance(ctx["top3"], list)
    assert isinstance(ctx["bottom3"], list)
    assert isinstance(ctx["segmentation"], dict)


@pytest.mark.asyncio
async def test_brief_context_deltas_none_without_previous_history(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "engine-brief-nohist")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp_1",
        campaign_name="Nueva", status="ACTIVE", budget=10.0,
        spend=50.0, conversions=5, impressions=500, clicks=20, date=today,
    ))
    await db_session.commit()

    engine = AIEngine(db_session)
    ctx = await engine._build_brief_context(acc)

    # Sin datos en la ventana previa (8-14 días atrás): delta no calculable.
    assert ctx["kpis_prev_7d"]["spend"] == 0
    assert ctx["deltas_pct"]["spend"] is None
