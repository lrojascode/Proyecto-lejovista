"""Reconciliación del estado real de la Saga dentro del chat (CH-1): GET
materializa progreso/resultado/error a partir de `CampaignDraft` sin
persistir; `POST /advance` sí persiste — es el fallback si el WebSocket
`campaign_creation` se pierde."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401
from tests.test_campaign_chat_gates import _create_session_at_review


def _turn_id() -> str:
    return str(uuid.uuid4())


async def _accept_to_creating(async_client, db_session, slug):
    headers, org_id, session_id, review_card = await _create_session_at_review(async_client, db_session, slug)
    accept_review = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": review_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    risk_card = next(m for m in accept_review.json()["messages"] if m["type"] == "risk_card")
    accept_risk = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": risk_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    session = accept_risk.json()
    return headers, org_id, session_id, session["draft_id"]


@pytest.mark.asyncio
async def test_get_materializes_result_when_draft_created(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, draft_id = await _accept_to_creating(async_client, db_session, "chat-saga-ready")

    draft = await db_session.get(CampaignDraft, draft_id)
    draft.status = "CREATED"
    draft.payload = {**(draft.payload or {}), "result": {"campaign_id": "cmp_123", "adset_id": "as_1", "ad_id": "ad_1"}}
    await db_session.commit()

    res = await async_client.get(f"/api/v1/campaigns/chat/sessions/{session_id}", headers=headers)
    session = res.json()
    result_msg = next(m for m in session["messages"] if m["type"] == "result")
    assert result_msg["campaign_id"] == "cmp_123"
    # GET no persiste: el estado de la sesión sigue como estaba.
    assert session["status"] == "OPEN"


@pytest.mark.asyncio
async def test_get_materializes_error_when_draft_failed(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, draft_id = await _accept_to_creating(async_client, db_session, "chat-saga-failed")

    draft = await db_session.get(CampaignDraft, draft_id)
    draft.status = "FAILED"
    draft.error_message = "Meta rechazó el adset."
    await db_session.commit()

    res = await async_client.get(f"/api/v1/campaigns/chat/sessions/{session_id}", headers=headers)
    session = res.json()
    error_msg = next(m for m in session["messages"] if m["type"] == "error")
    assert error_msg["code"] == "SAGA_FAILED"
    assert "Meta rechazó el adset" in error_msg["text"]


@pytest.mark.asyncio
async def test_get_shows_progress_while_pending(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, draft_id = await _accept_to_creating(async_client, db_session, "chat-saga-pending")

    res = await async_client.get(f"/api/v1/campaigns/chat/sessions/{session_id}", headers=headers)
    session = res.json()
    assert session["messages"][-1]["type"] == "progress"


@pytest.mark.asyncio
async def test_advance_persists_result_and_closes_session(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, session_id, draft_id = await _accept_to_creating(async_client, db_session, "chat-saga-advance")

    draft = await db_session.get(CampaignDraft, draft_id)
    draft.status = "CREATED"
    draft.payload = {**(draft.payload or {}), "result": {"campaign_id": "cmp_999"}}
    await db_session.commit()

    res = await async_client.post(f"/api/v1/campaigns/chat/sessions/{session_id}/advance", headers=headers)
    session = res.json()
    assert session["status"] == "CLOSED_CREATED"
    assert session["state"] == "DONE"
    result_msg = next(m for m in session["messages"] if m["type"] == "result")
    assert result_msg["campaign_id"] == "cmp_999"

    # Un segundo /advance no debe duplicar el mensaje de resultado.
    res2 = await async_client.post(f"/api/v1/campaigns/chat/sessions/{session_id}/advance", headers=headers)
    session2 = res2.json()
    result_msgs = [m for m in session2["messages"] if m["type"] == "result"]
    assert len(result_msgs) == 1


# --- Honestidad del resultado (14-ago-2026): con las escrituras a Meta
# apagadas, la Saga termina igual y el draft queda CREATED. Decir "¡Listo!" ahí
# es mentirle al usuario: la campaña no existe en Ads Manager. ---

def test_result_card_announces_a_simulated_creation():
    from app.services.campaign_chat import cards

    msg = cards.build_result_message(
        "draft-1", {"campaign_id": "mock_camp_1", "simulated": True}, "MI_CAMPANA",
    )
    assert msg["simulated"] is True
    assert "no se creó en Meta" in msg["text"]
    assert "¡Listo!" not in msg["text"]


def test_result_card_celebrates_a_real_creation():
    from app.services.campaign_chat import cards

    msg = cards.build_result_message(
        "draft-1", {"campaign_id": "120200000", "simulated": False}, "MI_CAMPANA",
    )
    assert msg["simulated"] is False
    assert "¡Listo!" in msg["text"]
