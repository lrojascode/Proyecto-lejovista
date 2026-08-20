"""El ping que valida una API Key tiene que hablar el dialecto del proveedor.

Viene de un fallo real en producción al cargar la credencial de OpenAI:

    Error 400 (openai): Unsupported parameter: 'max_tokens' is not supported
    with this model. Use 'max_completion_tokens' instead.

Los modelos de razonamiento de OpenAI (gpt-5.x, o1) dejaron de aceptar
`max_tokens`. Los endpoints compatibles de OpenRouter y Gemini siguen con el
nombre viejo, así que no alcanza con cambiarlo en todos lados.
"""
from unittest.mock import patch

import pytest

from app.config import settings
from app.services.ai.llm import OpenRouterClient


class _RespuestaOk:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


def _cliente_falso(capturado):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            capturado.update(json or {})
            return _RespuestaOk()

    return lambda **kw: FakeClient()


async def _payload_de_test_key(provider: str) -> dict:
    capturado: dict = {}
    with patch("httpx.AsyncClient", _cliente_falso(capturado)):
        cliente = OpenRouterClient(api_key="sk-test", provider=provider)
        ok, _ = await cliente.test_key()
    assert ok
    return capturado


@pytest.fixture(autouse=True)
def _sin_mock(monkeypatch):
    """`test_key` corta antes de la red en modo simulación."""
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)


@pytest.mark.asyncio
async def test_openai_usa_max_completion_tokens():
    payload = await _payload_de_test_key("openai")
    assert "max_completion_tokens" in payload
    assert "max_tokens" not in payload, "gpt-5.x responde 400 con max_tokens"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openrouter", "gemini_direct"])
async def test_los_demas_siguen_con_max_tokens(provider):
    payload = await _payload_de_test_key(provider)
    assert "max_tokens" in payload
    assert "max_completion_tokens" not in payload


@pytest.mark.asyncio
async def test_el_tope_de_openai_deja_lugar_al_razonamiento():
    """En un modelo de razonamiento el presupuesto lo consume primero la cadena
    de pensamiento; un tope de 5 puede devolver contenido vacío."""
    payload = await _payload_de_test_key("openai")
    assert payload["max_completion_tokens"] >= 16
