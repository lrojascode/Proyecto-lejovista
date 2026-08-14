"""Tope anti-bloqueo (máx. 5 campañas nuevas / 24h por organización) dentro
del chat — CH-1: debe avisarse ANTES de llegar a la compuerta final y
bloquear la creación con un mensaje legible, nunca un 500/429 crudo."""
import datetime
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from app.services.campaign_creation import MAX_CAMPAIGNS_PER_SESSION
from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401
from tests.test_campaign_chat_gates import _create_session_at_review


def _turn_id() -> str:
    return str(uuid.uuid4())


async def _seed_recent_drafts(db_session: AsyncSession, org_id: str, count: int):
    for i in range(count):
        db_session.add(CampaignDraft(
            organization_id=org_id, name=f"Filler {i}", payload={}, status="CREATED",
        ))
    await db_session.commit()


@pytest.fixture
def _writes_are_real(monkeypatch):
    """El tope solo aplica cuando las escrituras llegan de verdad a Meta — es a
    la cuenta de Meta a la que protege. La suite corre con MOCK_SERVICES=true,
    donde el tope está (correctamente) desactivado, así que para ejercitarlo
    hay que decir explícitamente que las escrituras son reales."""
    # Se parchea la CLASE: `settings` es un modelo de pydantic y no acepta que
    # se le cuelgue un método en la instancia.
    from app.config import Settings
    monkeypatch.setattr(Settings, "simulate_meta_writes_for", lambda self, *a, **kw: False)


@pytest.mark.asyncio
async def test_session_creation_warns_early_when_cap_already_reached(
    async_client: AsyncClient, db_session: AsyncSession, _writes_are_real
):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-cap-early"
    )
    await _seed_recent_drafts(db_session, org_id, MAX_CAMPAIGNS_PER_SESSION)

    res = await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )
    assert res.status_code == 400
    assert "límite" in res.json()["detail"].lower() or "campañas" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_confirm_blocks_with_readable_error_when_cap_reached_mid_conversation(
    async_client: AsyncClient, db_session: AsyncSession, _writes_are_real
):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-cap-mid"
    )
    # El tope se alcanza DESPUÉS de haber empezado la conversación (otra
    # sesión/el formulario crearon campañas mientras tanto).
    await _seed_recent_drafts(db_session, org_id, MAX_CAMPAIGNS_PER_SESSION)

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
    assert accept_risk.status_code == 200
    session = accept_risk.json()
    assert session["draft_id"] is None
    error_msg = next(m for m in session["messages"] if m["type"] == "error")
    assert error_msg["code"] == "CAP_REACHED"
    # La sesión sigue abierta: el usuario puede reintentar más tarde.
    assert session["status"] == "OPEN"


# --- Las campañas simuladas no cuentan contra el tope (14-ago-2026).
# Con el write-back apagado, 5 intentos dejaban el sistema trabado 24h
# mostrando "ya creaste 5 campañas" habiendo creado cero en Meta. ---

@pytest.mark.asyncio
async def test_simulated_writes_disable_the_cap_entirely(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Sin el fixture `_writes_are_real`: la suite corre simulada, que es
    justo el caso del bug. El tope protege la cuenta de Meta y acá no se toca
    Meta, así que no debe frenar nada."""
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-cap-simulado"
    )
    await _seed_recent_drafts(db_session, org_id, MAX_CAMPAIGNS_PER_SESSION * 3)

    res = await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_drafts_marked_simulated_do_not_count(db_session: AsyncSession, _writes_are_real):
    """Y una vez encendido el write-back, los intentos simulados de ayer
    tampoco pueden frenar la primera campaña real de hoy."""
    from app.models.organization import Organization
    from app.services.campaign_creation import count_recent_drafts

    org = Organization(slug="cap-simulados", name="Cap Simulados", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()

    for i in range(MAX_CAMPAIGNS_PER_SESSION):
        db_session.add(CampaignDraft(
            organization_id=org.id, name=f"Simulada {i}", status="CREATED",
            payload={"result": {"campaign_id": f"mock_camp_{i}", "simulated": True}},
        ))
    db_session.add(CampaignDraft(
        organization_id=org.id, name="Real", status="CREATED",
        payload={"result": {"campaign_id": "120200000", "simulated": False}},
    ))
    await db_session.commit()

    assert await count_recent_drafts(db_session, org.id) == 1


@pytest.mark.asyncio
async def test_a_mock_campaign_id_betrays_a_simulation_without_the_flag(db_session: AsyncSession):
    """Los drafts anteriores a que existiera `simulated` igual se reconocen por
    el prefijo `mock_` del campaign_id — si no, arreglar la config te dejaba
    bloqueado 24h por simulaciones viejas, que es justo lo que paso el
    14-ago-2026 al encender el write-back."""
    from app.models.organization import Organization
    from app.services.campaign_creation import count_recent_drafts

    org = Organization(slug="cap-sin-flag", name="Cap Sin Flag", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    for i in range(MAX_CAMPAIGNS_PER_SESSION):
        db_session.add(CampaignDraft(
            organization_id=org.id, name=f"Vieja simulada {i}", status="CREATED",
            payload={"result": {"campaign_id": f"mock_camp_17867156{i}"}},
        ))
    db_session.add(CampaignDraft(
        organization_id=org.id, name="Real", status="CREATED",
        payload={"result": {"campaign_id": "120200001"}},
    ))
    await db_session.commit()

    assert await count_recent_drafts(db_session, org.id) == 1


@pytest.mark.asyncio
async def test_in_flight_and_failed_drafts_still_count(db_session: AsyncSession):
    """Sin campaign_id no hay prueba de que NO haya llegado a Meta: se cuenta.
    Equivocarse para el lado de contar de mas es el error barato."""
    from app.models.organization import Organization
    from app.services.campaign_creation import count_recent_drafts

    org = Organization(slug="cap-en-vuelo", name="Cap En Vuelo", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    db_session.add(CampaignDraft(
        organization_id=org.id, name="En vuelo", status="PENDING", payload={},
    ))
    db_session.add(CampaignDraft(
        organization_id=org.id, name="Fallida", status="FAILED", payload={"result": {}},
    ))
    await db_session.commit()

    assert await count_recent_drafts(db_session, org.id) == 2
