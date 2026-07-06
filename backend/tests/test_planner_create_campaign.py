"""Tests de la integración de la Fase 3 en AIPlanner: el stub CREATE_CAMPAIGN
que hardcodeaba "THHN"/Lima/S/.2.50 para cualquier cuenta ahora usa
CampaignSuggester (3A, determinista) + OpenRouterClient.suggest_campaign_copy
(3B, LLM/mock) — sin cifras inventadas (AI-1)."""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.services.ai.planner import AIPlanner


async def _make_org_and_account(db: AsyncSession, slug="planner-cc-org"):
    org = Organization(slug=slug, name="Planner CC Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Althea Pack", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_create_campaign_action_uses_real_brand_and_no_hardcoded_thhn(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Envases Premium Lima", status="ACTIVE", budget=80.0, spend=40.0,
        conversions=20, impressions=5000, clicks=10, date=today,
    ))
    insight = AIInsight(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        level="WARNING", category="PERFORMANCE",
        title="Tasa de Clics (CTR) muy baja",
        description="La campaña 'Envases Premium Lima' tiene un CTR muy bajo.",
        suggested_action_type="CREATE_CAMPAIGN",
    )
    db_session.add(insight)
    await db_session.flush()

    actions = await AIPlanner(db_session).plan_actions(acc, [insight])
    create_actions = [a for a in actions if a.action_type == "CREATE_CAMPAIGN"]
    assert len(create_actions) == 1
    action = create_actions[0]

    # Marca real derivada de AdAccount.name ("Althea Pack"), no "MTV" hardcodeado.
    assert action.parameters["campaign_name"].startswith("ALTHEAPACK_MSJ_")
    assert "THHN" not in action.parameters["campaign_name"]
    assert "EnvasesPremiumLima" in action.parameters["campaign_name"]

    # Copy generado (mock local porque MOCK_SERVICES=true en tests) — no el
    # texto hardcodeado de cables eléctricos de antes.
    assert "Cables Eléctricos" not in action.parameters["creative"]["body"]
    assert action.parameters["creative"]["headline"]
    assert action.parameters["creative"]["welcome_message"]

    # Sin cifras inventadas: el impacto estimado es la evidencia real del suggester,
    # no el "CPL estimado de S/.2.50" fijo de antes.
    assert "S/.2.50" not in action.estimated_impact
    assert action.confidence == 0.75  # hay histórico (has_history=True)


@pytest.mark.asyncio
async def test_create_campaign_action_lowers_confidence_without_history(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="planner-cc-no-history")
    insight = AIInsight(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c-nohist",
        level="WARNING", category="PERFORMANCE",
        title="Tasa de Clics (CTR) muy baja",
        description="La campaña 'Nueva Marca' tiene un CTR muy bajo.",
        suggested_action_type="CREATE_CAMPAIGN",
    )
    db_session.add(insight)
    await db_session.flush()

    actions = await AIPlanner(db_session).plan_actions(acc, [insight])
    action = [a for a in actions if a.action_type == "CREATE_CAMPAIGN"][0]

    assert action.confidence == 0.5  # sin histórico -> menor confianza, honesto
