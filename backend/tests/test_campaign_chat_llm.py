"""Extractor de slots por LLM del chat de creación de campañas (CH-1):
`OpenRouterClient.extract_campaign_slots`. Bajo `MOCK_SERVICES` (lo que corre
en CI) siempre devuelve `None` — el motor cae al extractor determinista sin
romper la conversación."""
import json

import httpx
import pytest

from app.models.organization import Organization
from app.services.ai.llm import OpenRouterClient
from app.services.ai.token_tracking import UsageContext


class _FakeResponse:
    def __init__(self, status_code=200, content="{}"):
        self.status_code = status_code
        self._content = content
        self.text = content

    def json(self):
        return json.loads(self._content) if self._content.startswith("{") else {}


class _FakeAsyncClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.captured_json = json
        if self._exc:
            raise self._exc
        return self._response


def _org():
    return Organization(slug="chat-llm-org", name="Chat LLM Org", ai_model="gpt-5.4-mini")


@pytest.mark.asyncio
async def test_returns_none_under_mock_services(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    result = await OpenRouterClient().extract_campaign_slots(_org(), "hola", "ASK_GOAL", {})
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_without_api_key(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    result = await OpenRouterClient().extract_campaign_slots(_org(), "hola", "ASK_GOAL", {})
    assert result is None


@pytest.mark.asyncio
async def test_valid_json_is_parsed(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    content = json.dumps({"intent": "provide_info", "goal_text": "vender termas", "budget": None})
    response = _FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": content}}], "usage": {},
    }))
    fake_client = _FakeAsyncClient(response=response)
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    result = await OpenRouterClient().extract_campaign_slots(
        _org(), "quiero vender termas", "ASK_GOAL", {},
        usage_ctx=UsageContext(feature="campaign_chat", organization_id="org-1", ad_account_id="acc-1"),
    )
    # `budget: None` se conserva acá (el filtro es por CLAVE, no por valor) —
    # es el motor (`engine._try_llm_extraction`) quien descarta los None antes
    # de aplicarlos a la sesión.
    assert result == {"intent": "provide_info", "goal_text": "vender termas", "budget": None}


@pytest.mark.asyncio
async def test_json_wrapped_in_markdown_is_parsed(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    content = "```json\n" + json.dumps({"intent": "accept"}) + "\n```"
    response = _FakeResponse(200, json.dumps({"choices": [{"message": {"content": content}}], "usage": {}}))
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=response))

    result = await OpenRouterClient().extract_campaign_slots(_org(), "sí, dale", "REVIEW_PLAN", {})
    assert result == {"intent": "accept"}


@pytest.mark.asyncio
async def test_invalid_json_falls_back_to_none(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    response = _FakeResponse(200, json.dumps({"choices": [{"message": {"content": "no soy json"}}], "usage": {}}))
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=response))

    result = await OpenRouterClient().extract_campaign_slots(_org(), "algo raro", "ASK_GOAL", {})
    assert result is None


@pytest.mark.asyncio
async def test_unknown_intent_is_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    content = json.dumps({"intent": "not_a_real_intent"})
    response = _FakeResponse(200, json.dumps({"choices": [{"message": {"content": content}}], "usage": {}}))
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=response))

    result = await OpenRouterClient().extract_campaign_slots(_org(), "algo", "ASK_GOAL", {})
    assert result is None


@pytest.mark.asyncio
async def test_http_error_falls_back_to_none(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    response = _FakeResponse(500, "server error")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=response))

    result = await OpenRouterClient().extract_campaign_slots(_org(), "algo", "ASK_GOAL", {})
    assert result is None


@pytest.mark.asyncio
async def test_network_exception_falls_back_to_none(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(exc=httpx.ConnectError("boom")))

    result = await OpenRouterClient().extract_campaign_slots(_org(), "algo", "ASK_GOAL", {})
    assert result is None
