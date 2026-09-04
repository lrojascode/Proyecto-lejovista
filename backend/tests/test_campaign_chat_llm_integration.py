"""El motor del chat cae al LLM SOLO cuando el extractor determinista no
entendió el mensaje, y descarta cualquier cifra que el LLM proponga si no
hay un dígito literal en el texto del usuario (regla AI-1) — CH-1."""
import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401
from tests.test_campaign_chat_gates import _create_session_at_review


def _turn_id() -> str:
    return str(uuid.uuid4())


class _FakeResponse:
    def __init__(self, content: str):
        self.status_code = 200
        self.text = content
        self._content = content

    def json(self):
        return json.loads(self._content)


class _FakeAsyncClient:
    def __init__(self, content: str):
        self._content = content

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        return _FakeResponse(json_dumps_body(self._content))


def json_dumps_body(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}], "usage": {}})


@pytest.fixture(autouse=True)
def _llm_enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    # Desde la spec 10 el proveedor no se deduce de qué clave esté presente: se
    # declara al crear la organización. Estas pruebas crean la suya por la API,
    # así que el default tiene que apuntar al proveedor cuya credencial se
    # configura arriba — si no, la organización nace declarando OpenAI y la
    # clave de OpenRouter no le sirve, que es justo el criterio 8.
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openrouter")
    # `CampaignValidator.validate` real hace un GET a Meta; en el resto de los
    # tests eso se evita con MOCK_SERVICES=true, que acá deliberadamente
    # apagamos para probar la extracción LLM — así que se mockea aparte.
    return None


@pytest.mark.asyncio
async def test_llm_fills_copy_text_that_deterministic_extractor_cannot(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    llm_content = json.dumps({
        "intent": "change", "copy_text": "Compra ya y aprovecha el descuento", "budget": None, "days": None,
    })
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(llm_content))

    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-llm-copy"
    )
    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={
            "client_turn_id": _turn_id(), "card_id": review_card["card_id"],
            "decision": "change", "text": "el texto del anuncio deberia hablar de un descuento",
        },
        headers=headers,
    )
    session = res.json()
    assert session["slots"]["copy_text"] == "Compra ya y aprovecha el descuento"


@pytest.mark.asyncio
async def test_llm_budget_without_digits_in_user_text_is_discarded(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    # El LLM "alucina" una cifra que el usuario nunca escribió — el motor debe
    # descartarla igual (regla AI-1), sin importar qué devuelva el modelo.
    llm_content = json.dumps({"intent": "change", "budget": 99999, "days": None})
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(llm_content))

    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-llm-hallucination"
    )
    budget_before = next(f for f in review_card["fields"] if f["key"] == "budget")["value"]

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/decision",
        json={
            "client_turn_id": _turn_id(), "card_id": review_card["card_id"],
            "decision": "change", "text": "no estoy seguro de que esto alcance",
        },
        headers=headers,
    )
    session = res.json()
    new_review_card = next(m for m in reversed(session["messages"]) if m["type"] == "review_card")
    budget_after = next(f for f in new_review_card["fields"] if f["key"] == "budget")["value"]
    assert budget_after == budget_before
    assert "99999" not in budget_after
