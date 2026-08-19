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


# --- Segunda oportunidad para preguntas que los regex no reconocen
# (14-ago-2026). Caso reportado: "¿cuál es la capana activa ahora?" — un typo
# convertía una pregunta respondible en un rechazo. ---

_OUT_OF_SCOPE_START = "Eso se me escapa"


async def _open_session(async_client: AsyncClient, db_session: AsyncSession, slug: str):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(async_client, db_session, slug)
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )).json()["id"]
    return headers, session_id


async def _ask(async_client: AsyncClient, headers, session_id: str, text: str) -> str:
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": text}, headers=headers,
    )
    assert res.status_code == 200, res.text
    return "\n".join(m.get("text") or "" for m in res.json()["messages"])


@pytest.mark.asyncio
async def test_unrecognized_question_is_answered_when_the_llm_finds_the_topic(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.services.ai.llm import ChatPlan, OpenRouterClient

    async def fake_plan(self, org, text, available_campaigns=None, usage_ctx=None):
        return ChatPlan(blocks=["destination"], topic="DESTINATION")

    monkeypatch.setattr(OpenRouterClient, "plan_chat_request", fake_plan)

    headers, session_id = await _open_session(async_client, db_session, "chat-2nd-chance")
    transcript = await _ask(async_client, headers, session_id, "¿esto sirve para instagram tambien?")

    # Respondió con el dato real de la cuenta en vez de rechazar...
    assert "51999888777" in transcript
    assert _OUT_OF_SCOPE_START not in transcript
    # ...y dejó planteada de nuevo la pregunta pendiente.
    assert "promocionar" in transcript


@pytest.mark.asyncio
async def test_question_is_rejected_when_the_llm_says_out_of_scope(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Sin LLM (MOCK_SERVICES, el camino de CI) el comportamiento no cambia:
    fuera de tema se rechaza con el texto estático y a costo cero."""
    headers, session_id = await _open_session(async_client, db_session, "chat-still-rejects")
    transcript = await _ask(async_client, headers, session_id, "¿cuánto está el dólar hoy?")

    assert _OUT_OF_SCOPE_START in transcript


# --- Reescritura conversacional (CH-7): el LLM cambia el tono, nunca los datos ---

async def _with_rewriter(monkeypatch, rewrite: str):
    from app.services.ai.llm import OpenRouterClient

    async def fake_rephrase(self, org, canonical_text, user_message, usage_ctx=None):
        return rewrite

    monkeypatch.setattr(OpenRouterClient, "rephrase_chat_message", fake_rephrase)


@pytest.mark.asyncio
async def test_faithful_rewrite_reaches_the_user(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    await _with_rewriter(monkeypatch, "Genial. Mandame una foto del producto y seguimos 📸")

    headers, session_id = await _open_session(async_client, db_session, "chat-rewrite-ok")
    transcript = await _ask(async_client, headers, session_id, "quiero vender termas")

    assert "Mandame una foto del producto" in transcript


@pytest.mark.asyncio
async def test_rewrite_that_invents_a_number_is_discarded(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """El texto canónico no tiene ninguna cifra; la reescritura inventa "3
    fotos". Se descarta entera y el usuario ve el texto del código."""
    await _with_rewriter(monkeypatch, "Genial, mandame 3 fotos del producto y seguimos 📸")

    headers, session_id = await _open_session(async_client, db_session, "chat-rewrite-invented")
    transcript = await _ask(async_client, headers, session_id, "quiero vender termas")

    assert "3 fotos" not in transcript
    assert "súbeme la foto del producto" in transcript


@pytest.mark.asyncio
async def test_chat_works_when_the_rewriter_fails(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """La reescritura es cosmética: si el LLM explota, la conversación sigue."""
    from app.services.ai.llm import OpenRouterClient

    async def boom(self, org, canonical_text, user_message, usage_ctx=None):
        raise RuntimeError("proveedor caído")

    monkeypatch.setattr(OpenRouterClient, "rephrase_chat_message", boom)

    headers, session_id = await _open_session(async_client, db_session, "chat-rewrite-boom")
    transcript = await _ask(async_client, headers, session_id, "quiero vender termas")

    assert "súbeme la foto del producto" in transcript


# --- El LLM redacta las respuestas del historial (CH-8) ---

async def _with_analyst(monkeypatch, written: str):
    """Sustituye la redacción del LLM. `resolve_facts` se mockea para que
    devuelva `facts` sin depender de datos sincronizados."""
    from app.services.ai.llm import OpenRouterClient
    from app.services.campaign_chat import facts as facts_module
    from app.services.campaign_chat import questions

    async def fake_answer(db, org_id, ad_account, topic, slots, period=None):
        return "Llevás S/. 1085.47 de inversión.", {"spend": 1085.47, "conversations": 402}

    async def fake_resolve(db, org_id, ad_account, topic, slots=None, window_days=None):
        return {"spend": 1085.47, "conversations": 402}, "Llevás S/. 1085.47 de inversión."

    async def fake_compose(self, org, question, topic, facts, usage_ctx=None):
        return written

    monkeypatch.setattr(questions, "answer_question", fake_answer)
    monkeypatch.setattr(facts_module, "resolve_facts", fake_resolve)
    monkeypatch.setattr(OpenRouterClient, "compose_chat_answer", fake_compose)


@pytest.mark.asyncio
async def test_grounded_analysis_replaces_the_template(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    await _with_analyst(monkeypatch, "Vas bien: **402 conversaciones** y S/. 1085.47 invertidos. 👍")

    headers, session_id = await _open_session(async_client, db_session, "chat-analisis-ok")
    transcript = await _ask(async_client, headers, session_id, "¿cuánto gasté este mes?")

    assert "402 conversaciones" in transcript
    assert "👍" in transcript  # es la redacción del modelo, no la plantilla


@pytest.mark.asyncio
async def test_an_analysis_that_invents_a_figure_is_discarded(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """El costo por conversación no está en los datos: el modelo lo calculó.
    Se descarta la respuesta entera y se muestra la del código."""
    await _with_analyst(monkeypatch, "Gastaste S/. 1085.47 en 402 conversaciones, S/. 2.70 cada una.")

    headers, session_id = await _open_session(async_client, db_session, "chat-analisis-inventa")
    transcript = await _ask(async_client, headers, session_id, "¿cuánto gasté este mes?")

    assert "2.70" not in transcript
    assert "Llevás S/. 1085.47 de inversión." in transcript
