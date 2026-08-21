"""El LLM redacta las respuestas sobre el historial (CH-8).

`numbers_are_grounded` es lo único que separa "analista" de "inventa cifras
sobre la plata del cliente": el modelo elige qué contar, pero cada número que
escriba tiene que existir en los datos. Si esta guarda se ablanda, se cae la
garantía entera.
"""
import json

import httpx
import pytest

from app.models.organization import Organization
from app.services.ai.llm import OpenRouterClient
from app.services.campaign_chat.rephrase import grounded_or_canonical, numbers_are_grounded
from tests.test_campaign_chat_llm import _FakeAsyncClient, _FakeResponse, _key_env

_FACTS = json.dumps({
    "ventana": "los últimos 30 días",
    "dias_analizados": 30,
    "kpis_del_periodo": {"current": {"spend": 1085.47, "conversations": 402, "cost_per_conv": 2.70}},
    "campaigns": [{"name": "ALT_MSJ_LEADS_JUN290", "conversations": 219, "cost_per_conv": 0.58}],
}, ensure_ascii=False)

_CANONICO = "En los últimos 30 días llevás S/. 1085.47 de inversión."


def test_using_a_subset_of_the_figures_is_allowed():
    """Ahí está la libertad: el modelo elige qué destacar y qué omitir."""
    assert numbers_are_grounded(
        _FACTS,
        "Vas bien: **402 conversaciones** en 30 días. La que más te rinde es "
        "ALT_MSJ_LEADS_JUN290, a S/. 0.58 cada una.",
    ) is True


def test_mentioning_no_figures_at_all_is_allowed():
    assert numbers_are_grounded(_FACTS, "Venís bastante mejor que el mes pasado, seguí así.") is True


def test_an_invented_figure_is_rejected():
    assert numbers_are_grounded(_FACTS, "Llevás S/. 9999.00 de inversión.") is False


def test_a_figure_the_model_calculated_is_rejected():
    """El caso peligroso: no es un invento descarado sino una cuenta que parece
    razonable. 1085.47 / 402 no está en los datos, así que no puede escribirla —
    aunque el resultado fuera correcto, la cifra no salió de una query."""
    assert numbers_are_grounded(
        _FACTS, "Gastaste S/. 1085.47 en 402 conversaciones, o sea S/. 2.71 cada una.",
    ) is False


def test_a_rounded_figure_is_rejected():
    assert numbers_are_grounded(_FACTS, "Llevás unos S/. 1085 invertidos.") is False


def test_number_formatting_does_not_matter():
    """"1.085,47" y "1085.47" son el mismo número."""
    assert numbers_are_grounded(_FACTS, "Llevás S/ 1.085,47 invertidos.") is True


def test_links_are_not_allowed_in_an_analysis():
    assert numbers_are_grounded(_FACTS, "Mirá https://example.com/x para más detalle.") is False


@pytest.mark.parametrize("mala", ["", "   "])
def test_empty_answers_are_rejected(mala):
    assert numbers_are_grounded(_FACTS, mala) is False


def test_an_absurdly_long_answer_is_rejected():
    """Se ata a la constante y no a un largo fijo: el tope subio de 2000 a 4000
    cuando el prompt paso a pedir el formato de cuatro pasos con tabla, y este
    test se rompio por medir 4000 exactos."""
    from app.services.campaign_chat.grounding import MAX_ANALYSIS_CHARS

    assert numbers_are_grounded(_FACTS, "palabra " * (MAX_ANALYSIS_CHARS // 4)) is False


def test_grounded_or_canonical_falls_back():
    inventado = "Llevás S/. 9999.00 de inversión."
    assert grounded_or_canonical(_FACTS, _CANONICO, inventado) == _CANONICO
    bueno = "Vas invirtiendo S/. 1085.47 en el mes. Buen ritmo."
    assert grounded_or_canonical(_FACTS, _CANONICO, bueno) == bueno


# --- El método del LLM ---

def _org():
    return Organization(slug="chat-analisis", name="Chat Analisis", ai_model="gpt-5.4-mini")


def _answer_response(payload: str):
    return _FakeResponse(200, json.dumps({"choices": [{"message": {"content": payload}}], "usage": {}}))


@pytest.mark.asyncio
async def test_compose_returns_the_text(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(
        response=_answer_response(json.dumps({"text": "Llevás S/. 1085.47 este mes."}))))

    out = await OpenRouterClient().compose_chat_answer(
        _org(), "cuanto gaste este mes?", "SPEND", {"spend": 1085.47},
    )
    assert out == "Llevás S/. 1085.47 este mes."


@pytest.mark.asyncio
async def test_compose_returns_none_under_mock_services(monkeypatch):
    """Camino de CI: sin LLM, el chat responde con la plantilla de siempre."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    assert await OpenRouterClient().compose_chat_answer(
        _org(), "cuanto gaste?", "SPEND", {"spend": 1.0},
    ) is None


@pytest.mark.asyncio
async def test_compose_without_facts_does_not_call_the_model(monkeypatch):
    _key_env(monkeypatch)
    assert await OpenRouterClient().compose_chat_answer(_org(), "cuanto gaste?", "SPEND", {}) is None


@pytest.mark.asyncio
async def test_compose_survives_http_error_and_garbage(monkeypatch):
    _key_env(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=_FakeResponse(500, "boom")))
    assert await OpenRouterClient().compose_chat_answer(
        _org(), "x", "SPEND", {"spend": 1.0}) is None

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(response=_answer_response("no soy json")))
    assert await OpenRouterClient().compose_chat_answer(
        _org(), "x", "SPEND", {"spend": 1.0}) is None
