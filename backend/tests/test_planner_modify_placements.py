"""A-4: MODIFY_PLACEMENTS debe producir un payload válido para Meta —
publisher_platforms genérico + facebook_positions con las posiciones
concretas — en vez del `["facebook", "facebook_reels"]` inválido de antes
(facebook_reels es una posición, no una red). old_placements debe venir del
targeting real del AdsetSnapshot más reciente, no de una lista hardcodeada.
"""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.adset_snapshot import AdsetSnapshot
from app.models.ai_insight import AIInsight
from app.services.ai.planner import AIPlanner


async def _make_org_and_account(db: AsyncSession, slug="planner-mp-org"):
    org = Organization(slug=slug, name="Planner MP Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Planner MP Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_modify_placements_uses_facebook_positions_not_invalid_publisher_platform(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()
    db_session.add(AdsetSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1", adset_id="as1",
        name="Adset 1", budget=50.0,
        # Targeting real sincronizado: incluye Instagram (el problema real).
        targeting_summary={"publisher_platforms": ["facebook", "instagram"], "age_min": 25, "age_max": 65},
        date=today,
    ))
    insight = AIInsight(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        level="WARNING", category="PLACEMENT",
        title="Placement ineficiente en Instagram: instagram_positions",
        description="El placement 'instagram_positions' tiene un CPL de S/.9.50.",
        suggested_action_type="MODIFY_PLACEMENTS",
    )
    db_session.add(insight)
    await db_session.flush()

    actions = await AIPlanner(db_session).plan_actions(acc, [insight])
    assert len(actions) == 1
    action = actions[0]

    call_body = action.planned_api_calls[0]["body"]
    assert call_body["targeting"]["publisher_platforms"] == ["facebook"]
    # Ninguna posición inválida como "facebook_reels" dentro de publisher_platforms.
    assert "facebook_reels" not in call_body["targeting"]["publisher_platforms"]
    positions = call_body["targeting"]["facebook_positions"]
    assert "facebook_reels" in positions  # required_placements del tenant
    assert all("instagram" not in p for p in positions)

    # old_placements viene del snapshot REAL, no de una lista hardcodeada.
    assert action.parameters["old_placements"] == ["facebook", "instagram"]
    assert action.parameters["excluded_placement"] == "instagram_positions"
    assert action.parameters["new_placements"] == positions


@pytest.mark.asyncio
async def test_modify_placements_omitted_when_insight_has_no_flagged_placement(db_session: AsyncSession):
    """El insight de "oportunidad en Reels" no señala un placement a EXCLUIR
    (reels ya está siempre incluido por required_placements) — sin un
    placement concreto no se propone una acción a ciegas."""
    org, acc = await _make_org_and_account(db_session, "planner-mp-no-flag")
    today = datetime.date.today()
    db_session.add(AdsetSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c2", adset_id="as2",
        name="Adset 2", budget=50.0,
        targeting_summary={"publisher_platforms": ["facebook"]}, date=today,
    ))
    insight = AIInsight(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c2",
        level="INFO", category="PLACEMENT",
        title="Oportunidad detectada en Facebook Reels",
        description="El CPL en Facebook Reels es más barato que el feed.",
        suggested_action_type="MODIFY_PLACEMENTS",
    )
    db_session.add(insight)
    await db_session.flush()

    actions = await AIPlanner(db_session).plan_actions(acc, [insight])
    assert actions == []


@pytest.mark.asyncio
async def test_modify_placements_omitted_without_unambiguous_adset(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, "planner-mp-no-adset")
    insight = AIInsight(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c3",
        level="WARNING", category="PLACEMENT",
        title="Placement ineficiente en Instagram: instagram_positions",
        description="test",
        suggested_action_type="MODIFY_PLACEMENTS",
    )
    db_session.add(insight)
    await db_session.flush()

    # Sin ningún AdsetSnapshot para c3 -> 0 adsets vigentes -> se omite (AI-2).
    actions = await AIPlanner(db_session).plan_actions(acc, [insight])
    assert actions == []
