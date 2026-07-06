"""Tests de la Fase 3A del plan de creación de campañas: CampaignSuggester
deriva parámetros estructurales (objetivo, presupuesto, placements a excluir,
edad/dispositivo óptimos) de datos reales — nunca inventa cifras (AI-1)."""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.services.campaign_suggester import CampaignSuggester


async def _make_org_and_account(db: AsyncSession, slug="suggest-org"):
    org = Organization(slug=slug, name="Suggest Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Suggest Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_suggest_with_history_uses_real_winning_campaign(db_session: AsyncSession):
    from app.services.meta_ads.sync import MetaAdsSyncService
    org, acc = await _make_org_and_account(db_session, slug="suggest-with-history")
    await db_session.commit()
    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["has_history"] is True
    assert suggestion["winning_campaign"] is not None
    # La campaña "reels_better" del mock (camp_cable_gpt) es la más eficiente.
    assert "CABLE_GPT" in suggestion["winning_campaign"]["name"]
    assert suggestion["objective"] == "OUTCOME_LEADS"
    assert suggestion["destination_type"] == "WHATSAPP"
    assert suggestion["suggested_budget"] > 0
    assert any("más eficiente" in e for e in suggestion["evidence"])


@pytest.mark.asyncio
async def test_suggest_without_history_uses_documented_defaults(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="suggest-no-history")
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["has_history"] is False
    assert suggestion["winning_campaign"] is None
    assert suggestion["suggested_budget"] == 100.0  # default documentado, no derivado
    assert any("no se derivan de datos reales" in e.lower() for e in suggestion["evidence"])


@pytest.mark.asyncio
async def test_suggest_excludes_placements_with_spend_and_zero_conversions(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="suggest-exclude")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Winner", status="ACTIVE", budget=50.0, spend=50.0,
        conversions=25, impressions=1000, clicks=20, date=today,
        placements_breakdown={
            "facebook_feed": {"spend": 30.0, "conversions": 25},
            "audience_network": {"spend": 20.0, "conversions": 0},
        },
    ))
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["excluded_placements"] == ["audience_network"]
    assert any("audience network" in e for e in suggestion["evidence"])


@pytest.mark.asyncio
async def test_suggest_respects_explicit_budget_hint(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="suggest-budget-hint")
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc, budget_hint=350.0)

    assert suggestion["suggested_budget"] == 350.0
