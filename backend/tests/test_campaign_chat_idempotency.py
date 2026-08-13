"""Idempotencia del chat de creación de campañas (CH-1): una sesión crea como
máximo UNA campaña, incluso ante un doble clic o un reintento de red con el
mismo `client_turn_id`."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401
from tests.test_campaign_chat_gates import _create_session_at_review


def _turn_id() -> str:
    return str(uuid.uuid4())


async def _accept_to_risk(async_client, headers, session_id, review_card):
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": review_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    risk_card = next(m for m in res.json()["messages"] if m["type"] == "risk_card")
    return risk_card


@pytest.mark.asyncio
async def test_double_accept_creates_only_one_draft(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-idem-double"
    )
    risk_card = await _accept_to_risk(async_client, headers, session_id, review_card)

    first = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": risk_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    second = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": risk_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["draft_id"] == second.json()["draft_id"]

    count = (await db_session.execute(
        select(func.count()).select_from(CampaignDraft).where(CampaignDraft.organization_id == org_id)
    )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_replayed_client_turn_id_is_a_noop(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-idem-replay"
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )).json()["id"]

    turn_id = _turn_id()
    first = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": turn_id, "text": "vender termas"},
        headers=headers,
    )
    replay = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": turn_id, "text": "vender termas"},
        headers=headers,
    )
    assert first.json()["messages"] == replay.json()["messages"]
    assert len(replay.json()["messages"]) == len(first.json()["messages"])


@pytest.mark.asyncio
async def test_cannot_act_on_a_closed_session(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-idem-closed"
    )
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": review_card["card_id"], "decision": "reject"},
        headers=headers,
    )
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "en realidad sí quiero"},
        headers=headers,
    )
    assert res.status_code == 409
