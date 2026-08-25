"""Camino feliz del chat de creación de campañas de punta a punta (CH-1):
sesión → objetivo → foto → tarjeta de revisión (con los valores de
`CampaignSuggester`, prueba de que el LLM no eligió ninguna cifra) →
aceptar → tarjeta de riesgo → aceptar → CampaignDraft + CampaignLearningLog."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils import formatting
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.meta_page import MetaPage
from conftest import enlazar_paginas_y_numeros


async def _signup_with_ad_account_and_page(async_client: AsyncClient, db_session: AsyncSession, slug: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Chat Tester", "organization_slug": slug, "organization_name": "Chat Org",
    })
    body = signup_res.json()
    token = body["access_token"]
    org_id = body["user"]["organization_id"]
    headers = {"Authorization": f"Bearer {token}"}

    acc_res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={
            "platform": "META", "external_account_id": "act_1",
            "name": "Chat Acc", "access_token": "mock-token",
        },
        headers=headers,
    )
    ad_account_id = acc_res.json()["id"]

    db_session.add(MetaPage(
        organization_id=org_id, external_page_id="page_1", name="Mi Página",
        whatsapp_number="51999888777", status="ACTIVE",
    ))
    await db_session.flush()
    # El ALCANCE de una página es por cuenta, no por organización: se replica lo
    # que en producción hace el sync (o el seed de la migración).
    await enlazar_paginas_y_numeros(db_session, org_id)
    await db_session.commit()

    return headers, org_id, ad_account_id


@pytest.fixture(autouse=True)
def _mock_side_effects(mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    mocker.patch("app.services.campaign_creation.CampaignValidator.validate", autospec=True, return_value=None)
    mock_chain_factory = mocker.patch("app.services.campaign_creation.chain")
    mock_chain_instance = mock_chain_factory.return_value
    mock_chain_instance.on_error.return_value = mock_chain_instance
    return mock_chain_instance


def _turn_id() -> str:
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_full_chat_flow_creates_one_draft_and_learning_log(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-flow-org"
    )

    create_res = await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )
    assert create_res.status_code == 201, create_res.text
    session = create_res.json()
    assert session["state"] == "ASK_GOAL"
    assert session["messages"][0]["type"] == "text"

    session_id = session["id"]

    goal_res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "quiero vender mas zapatillas para hombre"},
        headers=headers,
    )
    assert goal_res.status_code == 200, goal_res.text
    session = goal_res.json()
    assert session["state"] == "ASK_MEDIA"
    assert session["slots"]["product_name"] == "Zapatillas Hombre"

    media_res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/foto.jpg"},
        headers=headers,
    )
    assert media_res.status_code == 200, media_res.text
    session = media_res.json()
    assert session["state"] == "REVIEW_PLAN"
    review_card = next(m for m in session["messages"] if m["type"] == "review_card")
    budget_field = next(f for f in review_card["fields"] if f["key"] == "budget")
    # El presupuesto mostrado tiene que venir de CampaignSuggester (evidencia de
    # que el LLM/extractor no inventó una cifra) — sin histórico, cae al
    # default determinista, pero SIGUE siendo lo que muestra `slots.budget`.
    # Se compara contra el formateador canónico, no contra un string armado a
    # mano: así el test sigue verificando que la tarjeta muestra el monto de la
    # sesión, sin quedar atado a cómo se escribe ese monto.
    assert formatting.money(session["slots"]["budget"]) in budget_field["value"]

    accept_review = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": review_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    assert accept_review.status_code == 200, accept_review.text
    session = accept_review.json()
    assert session["state"] == "CONFIRM_RISK"
    risk_card = next(m for m in session["messages"] if m["type"] == "risk_card")
    assert risk_card["risk_level"] in {"LOW", "MEDIUM", "HIGH"}

    accept_risk = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={"client_turn_id": _turn_id(), "card_id": risk_card["card_id"], "decision": "accept"},
        headers=headers,
    )
    assert accept_risk.status_code == 200, accept_risk.text
    session = accept_risk.json()
    assert session["state"] == "CREATING"
    assert session["draft_id"] is not None

    draft_count = (await db_session.execute(
        select(func.count()).select_from(CampaignDraft).where(CampaignDraft.organization_id == org_id)
    )).scalar_one()
    assert draft_count == 1

    log_count = (await db_session.execute(
        select(func.count()).select_from(CampaignLearningLog).where(CampaignLearningLog.organization_id == org_id)
    )).scalar_one()
    assert log_count == 1


@pytest.mark.asyncio
async def test_two_slots_in_one_message_skip_straight_to_media(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-skip-org"
    )
    create_res = await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )
    session_id = create_res.json()["id"]

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "quiero vender cables electricos"},
        headers=headers,
    )
    session = res.json()
    assert session["state"] == "ASK_MEDIA"

    res2 = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/x.jpg"},
        headers=headers,
    )
    assert res2.json()["state"] == "REVIEW_PLAN"
