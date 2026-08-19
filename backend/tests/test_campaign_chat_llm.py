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


# --- Clasificación de preguntas que los regex no reconocieron (14-ago-2026).
# El LLM acá NO responde: elige una etiqueta de un catálogo cerrado. Todo lo que
# se salga del catálogo se descarta, así que no puede colar una respuesta ni una
# cifra propia (regla AI-1). ---

def _key_env(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")


def _topic_response(payload: str):
    return _FakeResponse(200, json.dumps({
        "choices": [{"message": {"content": payload}}], "usage": {},
    }))


_TOPICS = {"SPEND": "cuánto se gastó", "CAMPAIGNS_OVERVIEW": "qué campañas tiene"}


@pytest.mark.asyncio
async def test_question_topic_is_classified(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({"topic": "CAMPAIGNS_OVERVIEW"}))))

    topic, period = await OpenRouterClient().classify_chat_question(
        _org(), "cual es la capana activa ahora?", _TOPICS,
        usage_ctx=UsageContext(feature="campaign_chat", organization_id="org-1", ad_account_id="acc-1"),
    )
    assert topic == "CAMPAIGNS_OVERVIEW"
    assert period is None  # la pregunta no menciona ningún lapso


@pytest.mark.asyncio
async def test_invented_topic_is_discarded(monkeypatch):
    """El catálogo es cerrado: si el modelo devuelve una etiqueta que no existe,
    se trata como fuera de tema en vez de reventar al buscar el contestador."""
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({"topic": "PRECIO_DEL_DOLAR"}))))

    assert await OpenRouterClient().classify_chat_question(_org(), "cuanto esta el dolar?", _TOPICS) == (None, None)


@pytest.mark.asyncio
async def test_null_topic_means_out_of_scope(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({"topic": None}))))

    assert await OpenRouterClient().classify_chat_question(_org(), "quien gano el mundial?", _TOPICS) == (None, None)


@pytest.mark.asyncio
async def test_classify_returns_none_under_mock_services(monkeypatch):
    """Es el camino de CI: sin LLM, el chat cae al rechazo estático de siempre."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    assert await OpenRouterClient().classify_chat_question(_org(), "cual es la capana activa?", _TOPICS) == (None, None)


@pytest.mark.asyncio
async def test_classify_survives_http_error(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=_FakeResponse(500, "boom")))
    assert await OpenRouterClient().classify_chat_question(_org(), "algo", _TOPICS) == (None, None)


# --- Reescritura conversacional (CH-7) ---

@pytest.mark.asyncio
async def test_rephrase_returns_none_under_mock_services(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    assert await OpenRouterClient().rephrase_chat_message(_org(), "Gastaste S/. 100.", "hola") is None


@pytest.mark.asyncio
async def test_rephrase_returns_the_text(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({"text": "Llevás S/. 100 gastados."}))))

    out = await OpenRouterClient().rephrase_chat_message(
        _org(), "Gastaste S/. 100.", "cuanto gaste?",
        usage_ctx=UsageContext(feature="campaign_chat", organization_id="org-1", ad_account_id="acc-1"),
    )
    assert out == "Llevás S/. 100 gastados."


@pytest.mark.asyncio
async def test_rephrase_of_empty_text_does_not_call_the_model(monkeypatch):
    _key_env(monkeypatch)
    assert await OpenRouterClient().rephrase_chat_message(_org(), "   ", "hola") is None


@pytest.mark.asyncio
async def test_rephrase_survives_garbage_json(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=_topic_response("no soy json")))
    assert await OpenRouterClient().rephrase_chat_message(_org(), "Gastaste S/. 100.", "hola") is None


@pytest.mark.asyncio
async def test_period_is_taken_only_from_the_closed_enum(monkeypatch):
    """El modelo elige una ETIQUETA de período, nunca un entero. Una que no
    esté en el enum se descarta sin invalidar el tema: se responde con la
    ventana por defecto, que es mejor que no responder."""
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({"topic": "SPEND", "period": "30d"}))))
    assert await OpenRouterClient().classify_chat_question(
        _org(), "cuanto gaste este mes?", _TOPICS, periods=["7d", "30d"],
    ) == ("SPEND", "30d")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({"topic": "SPEND", "period": "37 dias"}))))
    assert await OpenRouterClient().classify_chat_question(
        _org(), "cuanto gaste?", _TOPICS, periods=["7d", "30d"],
    ) == ("SPEND", None)


@pytest.mark.asyncio
async def test_plan_chat_request_returns_valid_blocks(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_topic_response(json.dumps({
            "blocks": ["kpis", "ranking", "invalid_block"],
            "period_key": "30d",
            "period_b_key": "90d",
            "wants_creation": False,
        }))
    ))
    plan = await OpenRouterClient().plan_chat_request(
        _org(), "comparame el gasto y las mejores campañas de julio vs agosto",
        available_campaigns=["Camp1", "Camp2"],
    )
    assert plan.blocks == ["kpis", "ranking"]
    assert plan.period_key == "30d"
    assert plan.period_b_key == "90d"
    assert plan.wants_creation is False
