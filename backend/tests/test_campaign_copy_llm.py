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
        self.captured_json = json
        if self._exc:
            raise self._exc
        return self._response


@pytest.mark.asyncio
async def test_suggest_campaign_copy_uses_mock_when_mock_services_enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    org, acc = _org_and_acc()

    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS
    assert len(result["headline"]) == 2 and len(result["primary_text"]) == 2
    assert "Cable GPT" in result["headline"][0]
    assert all(len(h) <= 40 for h in result["headline"])
    assert len(result["description"]) <= 30


@pytest.mark.asyncio
async def test_suggest_campaign_copy_parses_valid_llm_json(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    llm_json = json.dumps({
        "headline": ["LLM Headline A", "LLM Headline B"], "primary_text": ["LLM body A", "LLM body B"],
        "description": "LLM desc", "welcome_message": "LLM welcome",
    })
    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": llm_json}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert result == {
        "headline": ["LLM Headline A", "LLM Headline B"], "primary_text": ["LLM body A", "LLM body B"],
        "description": "LLM desc", "welcome_message": "LLM welcome",
    }


@pytest.mark.asyncio
async def test_suggest_campaign_copy_tolerates_llm_wrapping_json_in_markdown(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    wrapped = "```json\n" + json.dumps({
        "headline": ["H1", "H2"], "primary_text": ["P1", "P2"], "description": "D", "welcome_message": "W",
    }) + "\n```"
    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": wrapped}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert result["headline"] == ["H1", "H2"]


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
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS
    assert "Cable GPT" in result["headline"][0]


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_when_variant_count_wrong(monkeypatch):
    """3.4: si el LLM no manda exactamente 2 variantes, se descarta y cae al mock."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    llm_json = json.dumps({
        "headline": ["Solo una variante"], "primary_text": ["P1", "P2"],
        "description": "D", "welcome_message": "W",
    })
    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": llm_json}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert len(result["headline"]) == 2  # cayó al mock, que sí trae 2


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_when_headline_exceeds_meta_limit(monkeypatch):
    """3.4(c): un headline que excede el límite de Meta se rechaza, no se pasa tal cual."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    too_long = "H" * 41
    llm_json = json.dumps({
        "headline": [too_long, "Headline corto"], "primary_text": ["P1", "P2"],
        "description": "D", "welcome_message": "W",
    })
    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": llm_json}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert too_long not in result["headline"]  # cayó al mock


@pytest.mark.asyncio
async def test_suggest_campaign_copy_injects_organization_industry(monkeypatch):
    """3.4(a): el rubro de la organización se inyecta en el prompt."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": json.dumps({
            "headline": ["H1", "H2"], "primary_text": ["P1", "P2"],
            "description": "D", "welcome_message": "W",
        })}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    org.industry = "Ferretería"
    await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    user_message = next(m["content"] for m in fake_client.captured_json["messages"] if m["role"] == "user")
    assert "Ferretería" in user_message


@pytest.mark.asyncio
async def test_suggest_campaign_copy_requests_json_response_format(monkeypatch):
    """3.4(d): response_format json_object cuando el proveedor lo soporta."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": json.dumps({
            "headline": ["H1", "H2"], "primary_text": ["P1", "P2"],
            "description": "D", "welcome_message": "W",
        })}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert fake_client.captured_json["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_on_http_error(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(response=_FakeResponse(500, "Internal Server Error"))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS


@pytest.mark.asyncio
async def test_suggest_campaign_copy_falls_back_on_network_exception(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(exc=httpx.ConnectError("boom"))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS


@pytest.mark.asyncio
async def test_suggest_campaign_copy_no_api_key_uses_mock(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    org, acc = _org_and_acc()
    result = await OpenRouterClient().suggest_campaign_copy(org, acc, "Cable GPT")

    assert set(result.keys()) == _REQUIRED_COPY_KEYS


@pytest.mark.asyncio
async def test_suggest_campaign_copy_includes_winning_ad_copy_in_prompt(monkeypatch):
    """Fase 6b: el copy real del anuncio ganador (capturado por el sync) debe
    llegar al prompt como inspiración fuerte, no solo el nombre/costo genérico
    de winning_context."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    fake_client = _FakeAsyncClient(response=_FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": json.dumps({
            "headline": ["H1", "H2"], "primary_text": ["P1", "P2"], "description": "D", "welcome_message": "W",
        })}}]
    })))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    org, acc = _org_and_acc()
    await OpenRouterClient().suggest_campaign_copy(
        org, acc, "Cable GPT",
        winning_ad_copy={
            "title": "El copy que ya funcionó",
            "body": "Cuerpo real ganador con gancho probado",
            "cta_type": "WHATSAPP_MESSAGE",
        },
    )

    sent_messages = fake_client.captured_json["messages"]
    user_message = next(m["content"] for m in sent_messages if m["role"] == "user")
    assert "El copy que ya funcionó" in user_message
    assert "Cuerpo real ganador con gancho probado" in user_message
