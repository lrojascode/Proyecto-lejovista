"""Compuertas del chat (rechazar, cambiar de opinión, pregunta fuera de
tema) — CH-1."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401


def _turn_id() -> str:
    return str(uuid.uuid4())


async def _create_session_at_review(async_client: AsyncClient, db_session: AsyncSession, slug: str):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(async_client, db_session, slug)
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )).json()["id"]
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "vender termas"},
        headers=headers,
    )
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/x.jpg"},
        headers=headers,
    )
    session = res.json()
    review_card = next(m for m in session["messages"] if m["type"] == "review_card")
    return headers, org_id, session_id, review_card


@pytest.mark.asyncio
async def test_reject_at_review_creates_nothing(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-reject-review"
    )
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": review_card["card_id"], "decision": "reject"},
        headers=headers,
    )
    session = res.json()
    assert session["status"] == "CLOSED_CANCELLED"
    assert session["draft_id"] is None

    count = (await db_session.execute(
        select(func.count()).select_from(CampaignDraft).where(CampaignDraft.organization_id == org_id)
    )).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_reject_at_risk_creates_nothing(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-reject-risk"
    )
    accept_res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": review_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    risk_card = next(m for m in accept_res.json()["messages"] if m["type"] == "risk_card")

    reject_res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": risk_card["card_id"], "decision": "reject"},
        headers=headers,
    )
    session = reject_res.json()
    assert session["status"] == "CLOSED_CANCELLED"
    assert session["draft_id"] is None

    count = (await db_session.execute(
        select(func.count()).select_from(CampaignDraft).where(CampaignDraft.organization_id == org_id)
    )).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_change_budget_updates_review_card_and_clears_acceptance(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-change-budget"
    )
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={
            "client_turn_id": _turn_id(), "card_id": review_card["card_id"],
            "decision": "change", "text": "poné 300 soles",
        },
        headers=headers,
    )
    session = res.json()
    assert session["state"] == "REVIEW_PLAN"
    assert session["slots"]["budget"] == 300.0
    # La tarjeta más reciente (no la primera: ya había una del BUILD_PLAN original).
    new_review_card = next(m for m in reversed(session["messages"]) if m["type"] == "review_card")
    budget_field = next(f for f in new_review_card["fields"] if f["key"] == "budget")
    assert "300" in budget_field["value"]


@pytest.mark.asyncio
async def test_off_topic_question_does_not_change_state(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-off-topic"
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )).json()["id"]

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "vender termas"},
        headers=headers,
    )
    state_before = res.json()["state"]
    slots_before = res.json()["slots"]

    res2 = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "¿cuánto está el dólar hoy?"},
        headers=headers,
    )
    session = res2.json()
    assert session["state"] == state_before
    assert session["slots"] == slots_before
    assert session["messages"][-1]["type"] == "text"
