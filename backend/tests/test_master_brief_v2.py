"""C-3: Master Brief v2 — prompt real con contexto enriquecido (target_cpl,
comparativa 7d vs 7d previos, top/bottom, pacing) y contrato de salida fijo;
más el filtrado del aviso "histórico insuficiente" (ruido para el LLM).
"""
import pytest
import httpx

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.ai.llm import OpenRouterClient, PROMPT_VERSION, _filter_insufficient_history


def test_prompt_version_is_v2():
    assert PROMPT_VERSION == "master-brief-v2"


def test_filter_insufficient_history_removes_only_that_line():
    raw = (
        "- [WARNING] [PERFORMANCE] CPL alto: la campaña X excede el objetivo.\n"
        "- [INFO] [ACCOUNT] Histórico insuficiente para análisis estadístico: 'Y' (3d).\n"
        "- [CRITICAL] [PERFORMANCE] Conversiones en cero: la campaña Z no convierte."
    )
    filtered = _filter_insufficient_history(raw)
    assert "Histórico insuficiente" not in filtered
    assert "CPL alto" in filtered
    assert "Conversiones en cero" in filtered


def test_filter_insufficient_history_empty_falls_back_to_placeholder():
    only_noise = "- [INFO] [ACCOUNT] Histórico insuficiente para análisis estadístico: 'Y' (3d)."
    assert _filter_insufficient_history(only_noise) == "Sin alertas relevantes en esta ventana."


@pytest.mark.asyncio
async def test_real_prompt_includes_enriched_context_and_output_contract(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")

    captured = {}

    class _FakeResponse:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "narrativa"}}], "usage": {}}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    org = Organization(slug="mb2-org", name="MB2 Org", ai_model="google/gemini-2.5-flash")
    acc = AdAccount(organization_id="o", platform="META", external_account_id="1", name="Acc")
    brief_context = {
        "currency": "PEN", "country": "Perú", "target_cpl": 4.5,
        "kpis_7d": {"spend": 100.0, "conversations": 20, "cost_per_conv": 5.0},
        "kpis_prev_7d": {"spend": 80.0, "conversations": 25, "cost_per_conv": 3.2},
        "deltas_pct": {"spend": 25.0, "conversations": -20.0, "cost_per_conv": 56.3},
        "top3": [{"name": "Ganadora", "cost_per_conv": 2.0, "conversations": 10, "label": "GANADORA"}],
        "bottom3": [{"name": "Fallida", "cost_per_conv": 20.0, "conversations": 0, "label": "PAUSAR"}],
        "pacing": {"daily_budget_total": 50.0, "daily_spend_avg": 14.3},
        "segmentation": {"placements": {"feed": {"spend": 10.0, "conversions": 3}}},
    }

    narrative, used_fallback = await OpenRouterClient().analyze_campaigns(
        org, acc, "- [WARNING] alerta relevante", {}, brief_context=brief_context,
    )

    assert used_fallback is False
    user_prompt = captured["payload"]["messages"][1]["content"]
    # Contexto enriquecido presente.
    assert "target_cpl" in user_prompt.lower() or "4.5" in user_prompt
    assert "Perú" in user_prompt and "PEN" in user_prompt
    assert "Ganadora" in user_prompt and "Fallida" in user_prompt
    assert "pacing" in user_prompt.lower()
    # Contrato de salida fijo.
    for section in ("## Resumen", "## Ganadoras", "## En alerta", "## Fallidas", "## Prioridades de la semana"):
        assert section in user_prompt
    # El system prompt exige clasificar solo contra el target_cpl provisto.
    system_prompt = captured["payload"]["messages"][0]["content"]
    assert "target_cpl" in system_prompt
