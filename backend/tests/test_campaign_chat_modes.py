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
    mocker.patch("app.services.campaign_chat.questions.days_of_history", return_value=30)
    mocker.patch(
        "app.services.campaign_chat.questions.get_kpis_with_deltas",
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
