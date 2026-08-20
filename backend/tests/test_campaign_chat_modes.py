"""Tests para los modos ASK y BUILD y control de permisos en el chat (Fase 5.3 y 5.6)."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User


async def _auth_headers_for_role(async_client: AsyncClient, db: AsyncSession, org, email: str, role: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123",
        "full_name": "Test User", "organization_slug": f"{org.slug}-{email.split('@')[0]}",
        "organization_name": "Personal Org",
    })
    token = signup_res.json()["access_token"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role=role))
    await db.commit()
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug}


@pytest.mark.asyncio
async def test_viewer_can_create_ask_session_and_is_rejected_on_build(
    async_client: AsyncClient, db_session: AsyncSession
):
    org = Organization(slug="chat-modes-org", name="Chat Modes Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()

    acc = AdAccount(
        id="acc_modes_1",
        organization_id=org.id,
        name="Cuenta Modes",
        platform="META",
        external_account_id="act_111222333",
        status="ACTIVE",
    )
    db_session.add(acc)
    await db_session.commit()

    v_headers = await _auth_headers_for_role(async_client, db_session, org, "viewer@example.com", "VIEWER")

    # 1. VIEWER no puede crear sesión en BUILD (403)
    res_build = await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        headers=v_headers,
        json={"ad_account_id": acc.id, "mode": "BUILD"},
    )
    assert res_build.status_code == 403

    # 2. VIEWER sí puede crear sesión en ASK (201)
    res_ask = await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        headers=v_headers,
        json={"ad_account_id": acc.id, "mode": "ASK"},
    )
    assert res_ask.status_code == 201
    data = res_ask.json()
    assert data["mode"] == "ASK"
    assert data["state"] == "BROWSING"
    assert len(data["messages"]) > 0
    assert "quick_replies" in data["messages"][0]


@pytest.mark.asyncio
async def test_ask_mode_answering_questions_does_not_push_creation(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    for mod in ("questions", "facts"):
        mocker.patch(f"app.services.campaign_chat.{mod}.days_of_history", return_value=30)
        mocker.patch(
            f"app.services.campaign_chat.{mod}.get_kpis_with_deltas",
            return_value={
                "current": {"spend": 800.0, "conversations": 30, "cost_per_conv": 26.6},
                "previous": {"spend": 700.0, "conversations": 25, "cost_per_conv": 28.0},
                "deltas_pct": {"spend": 14.3, "conversations": 20.0, "cost_per_conv": -5.0},
            },
        )

    org = Organization(slug="chat-modes-org2", name="Chat Modes Org 2", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()

    acc = AdAccount(
        id="acc_modes_2",
        organization_id=org.id,
        name="Cuenta Modes 2",
        platform="META",
        external_account_id="act_222333444",
        status="ACTIVE",
    )
    db_session.add(acc)
    await db_session.commit()

    v_headers = await _auth_headers_for_role(async_client, db_session, org, "viewer2@example.com", "VIEWER")

    # Crear sesión ASK
    res = await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        headers=v_headers,
        json={"ad_account_id": acc.id, "mode": "ASK"},
    )
    session_id = res.json()["id"]

    # Preguntar gasto
    msg_res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        headers=v_headers,
        json={"client_turn_id": "turn_1", "text": "¿Cuánto gasté este mes?"},
    )
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert msg_data["state"] == "BROWSING"
    
    # La respuesta no debe tener el remate "¿Qué querés promocionar?"
    all_text = " ".join(m.get("text", "") for m in msg_data["messages"])
    assert "¿Qué querés promocionar?" not in all_text
    assert "S/. 800.00" in all_text


@pytest.mark.asyncio
async def test_h6_llm_budget_by_mode(db_session: AsyncSession):
    """H6: Presupuestos de LLM diferenciados por modo (BUILD vs ASK)."""
    from app.models.campaign_chat_session import CampaignChatSession
    from app.services.campaign_chat.engine import (
        MAX_ANALYSIS_TURNS,
        MAX_UNDERSTANDING_CALLS,
        _max_analysis_turns,
        _max_understanding_calls,
        _try_llm_plan,
    )

    assert MAX_UNDERSTANDING_CALLS["BUILD"] == 12
    assert MAX_UNDERSTANDING_CALLS["ASK"] == 40
    assert MAX_ANALYSIS_TURNS["BUILD"] == 30
    assert MAX_ANALYSIS_TURNS["ASK"] == 60

    sess_build = CampaignChatSession(
        organization_id="org1", created_by_user_id="u1", ad_account_id="acc1",
        mode="BUILD", state="ASK_GOAL", llm_calls=12,
    )
    sess_ask = CampaignChatSession(
        organization_id="org1", created_by_user_id="u1", ad_account_id="acc1",
        mode="ASK", state="BROWSING", llm_calls=12,
    )

    assert _max_understanding_calls(sess_build) == 12
    assert _max_understanding_calls(sess_ask) == 40
    assert _max_analysis_turns(sess_build) == 30
    assert _max_analysis_turns(sess_ask) == 60

    # En BUILD con 12 llamadas, no permite más calls
    plan, period = await _try_llm_plan(db_session, types_user("org1"), sess_build, "consulta")
    assert plan is None


def types_user(org_id: str):
    import types
    return types.SimpleNamespace(id="u1", organization_id=org_id)


@pytest.mark.asyncio
async def test_h6_persisted_messages_capped_at_200(async_client: AsyncClient, db_session: AsyncSession, mocker):
    """H6: messages_json se acota a MAX_MESSAGES_PERSISTED = 200."""
    from app.models.campaign_chat_session import CampaignChatSession
    from app.services.campaign_chat.engine import MAX_MESSAGES_PERSISTED

    org = Organization(slug="chat-cap-org", name="Chat Cap Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()

    acc = AdAccount(
        id="acc_cap_1", organization_id=org.id, name="Cuenta Cap",
        platform="META", external_account_id="act_999888", status="ACTIVE",
    )
    db_session.add(acc)
    await db_session.flush()

    from app.services.campaign_chat import cards
    # Pre-cargar una sesión con 205 mensajes
    large_messages = [cards.user_text_message(f"Msg {i}") for i in range(205)]
    sess = CampaignChatSession(
        organization_id=org.id, created_by_user_id=None, ad_account_id=acc.id,
        mode="ASK", state="BROWSING", status="OPEN",
        messages_json=large_messages, turn_count=205,
    )
    db_session.add(sess)
    await db_session.commit()

    v_headers = await _auth_headers_for_role(async_client, db_session, org, "viewer_cap@example.com", "VIEWER")

    # Enviar un mensaje nuevo
    msg_res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{sess.id}/messages",
        headers=v_headers,
        json={"client_turn_id": "turn_cap_1", "text": "¿cómo voy?"},
    )
    assert msg_res.status_code == 200
    await db_session.refresh(sess)
    assert len(sess.messages_json) <= MAX_MESSAGES_PERSISTED
