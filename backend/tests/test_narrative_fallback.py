"""C-4: el fallback del LLM ya no se disfraza de narrativa generada por IA.

`analyze_campaigns` devuelve (narrativa, used_fallback) y el engine persiste
`narrative_provider="fallback_template"` / `narrative_model=None` cuando la
narrativa salió de la plantilla local (mock activo, sin API key, o error del
proveedor). Antes se estampaba el modelo/proveedor configurado igual, y la UI
mostraba un template con el badge "Procesado por IA".
"""
import json
import pytest
import httpx

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.ai.llm import OpenRouterClient


def _org_acc():
    org = Organization(slug="fb-org", name="Fallback Org", ai_model="google/gemini-2.5-flash")
    acc = AdAccount(organization_id="o", platform="META", external_account_id="1", name="Acc")
    return org, acc


@pytest.mark.asyncio
async def test_mock_mode_marks_fallback():
    # conftest fija MOCK_SERVICES=true: no se llama al LLM.
    org, acc = _org_acc()
    narrative, used_fallback = await OpenRouterClient().analyze_campaigns(org, acc, "sin alertas", {})
    assert used_fallback is True
    assert narrative  # la plantilla local siempre entrega algo


@pytest.mark.asyncio
async def test_llm_success_is_not_fallback(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")

    class _FakeResponse:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "análisis real del LLM"}}], "usage": {}}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None): return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    org, acc = _org_acc()
    narrative, used_fallback = await OpenRouterClient().analyze_campaigns(org, acc, "alertas", {})
    assert used_fallback is False
    assert narrative == "análisis real del LLM"


@pytest.mark.asyncio
async def test_llm_error_marks_fallback(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")

    class _FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            raise httpx.ConnectError("LLM caído")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    org, acc = _org_acc()
    narrative, used_fallback = await OpenRouterClient().analyze_campaigns(org, acc, "alertas", {})
    assert used_fallback is True
    assert narrative  # degradación con contenido, nunca vacío
