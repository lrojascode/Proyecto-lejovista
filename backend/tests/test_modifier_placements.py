"""A-4: MetaCampaignModifier debe escribir facebook_positions, no meter
posiciones (p.ej. "reels") dentro de publisher_platforms — el bug real que
Meta rechazaba en producción.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.services.meta_ads.modifier import MetaCampaignModifier


@pytest.fixture
async def approved_placements_action(init_db, db_session: AsyncSession):
    org = Organization(slug="mp-mod-org", name="Modifier Placements Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="66622", name="Modifier Acc", status="ACTIVE",
    )
    acc.set_access_token("mock_access_token")
    db_session.add(acc)
    await db_session.flush()

    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id,
        action_type="MODIFY_PLACEMENTS", risk_level="LOW",
        title="Optimizar Placements (remover instagram positions)", description="test",
        target_campaign_id="camp_1", target_adset_id="adset_1", platform="META",
        parameters={
            "old_placements": ["facebook", "instagram"],
            "new_placements": ["feed", "story", "facebook_reels"],
            "excluded_placement": "instagram_positions",
        },
        planned_api_calls=[], status="APPROVED",
    )
    db_session.add(action)
    await db_session.commit()
    return acc, action


@pytest.mark.asyncio
async def test_modify_writes_facebook_positions_not_publisher_platform(
    approved_placements_action, db_session: AsyncSession
):
    acc, action = approved_placements_action

    result = await MetaCampaignModifier(db_session).modify(acc, action)

    assert result is not None  # SIMULATE_META_WRITES=true en tests: llamada simulada, no error


@pytest.mark.asyncio
async def test_modify_placements_post_data_shape(approved_placements_action, db_session, mocker):
    acc, action = approved_placements_action
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"success": True}

    await MetaCampaignModifier(db_session).modify(acc, action)

    # Segunda llamada (la primera, en modo simulado, no pega a la red para el
    # backup; solo la escritura real pasa por call_api mockeado).
    _, endpoint, post_data = mock_call_api.call_args[0]
    kwargs = mock_call_api.call_args[1]
    assert kwargs["method"] == "POST"
    targeting = post_data["targeting"]
    assert targeting["publisher_platforms"] == ["facebook"]
    assert "facebook_reels" in targeting["facebook_positions"]
    assert all("instagram" not in p for p in targeting["facebook_positions"])
    # Nunca una posición inválida colada en publisher_platforms.
    assert "facebook_reels" not in targeting["publisher_platforms"]
    assert "story" not in targeting["publisher_platforms"]
