"""Tests de la Fase 3B del plan de creación de campañas: OpenRouterClient.
suggest_campaign_copy redacta el copy (headline/primary_text/description/
welcome_message) vía LLM, con fallback determinista si falla o no hay API key.
"""
import json
import pytest
import httpx

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.ai.llm import OpenRouterClient, _REQUIRED_COPY_KEYS


def _org_and_acc():
    org = Organization(slug="copy-org", name="Copy Org", ai_model="gpt-5.4-mini")
    acc = AdAccount(organization_id="o", platform="META", external_account_id="1", name="Copy Acc")
    return org, acc


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
        if self._exc:
            raise self._exc
        return self._response


@pytest.mark.asyncio
async def test_suggest_campaign_copy_uses_mock_when_mock_services_enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    org, acc = _org_and_acc()

    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS
    assert "Cable GPT" in result["headline"]
    assert "Surquillo" in result["headline"]


@pytest.mark.asyncio
async def test_suggest_campaign_copy_parses_valid_llm_json(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    llm_json = json.dumps({
        "headline": "LLM Headline", "primary_text": "LLM body",
        "description": "LLM desc", "welcome_message": "LLM welcome",
    })
    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": llm_json}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert result == {
        "headline": "LLM Headline", "primary_text": "LLM body",
        "description": "LLM desc", "welcome_message": "LLM welcome",
    }


@pytest.mark.asyncio
async def test_suggest_campaign_copy_tolerates_llm_wrapping_json_in_markdown(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    wrapped = "```json\n" + json.dumps({
        "headline": "H", "primary_text": "P", "description": "D", "welcome_message": "W",
    }) + "\n```"
    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": wrapped}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert result["headline"] == "H"


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_on_invalid_json(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": "esto no es JSON"}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS
    assert "Cable GPT" in result["headline"]


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_on_http_error(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(response=_FakeResponse(500, "Internal Server Error"))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_on_network_exception(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(exc=httpx.ConnectError("boom"))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS


@pytest.mark.asyncio
async def test_suggest_campaign_copy_no_api_key_uses_mock(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT", "Surquillo")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS
