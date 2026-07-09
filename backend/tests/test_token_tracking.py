"""Tests del contador de tokens (Ajuste 3): estimación de costo y registro."""
import pytest
from sqlalchemy import select

from app.services.ai.token_tracking import estimate_cost_usd, record_token_usage, UsageContext
from app.models.ai_token_usage import AITokenUsage


def test_estimate_cost_known_model():
    # gemini-2.5-flash: input 0.15 / output 0.60 por 1M.
    cost = estimate_cost_usd("gemini-2.5-flash", 1_000_000, 1_000_000)
    assert cost == round(0.15 + 0.60, 6)


def test_estimate_cost_strips_openrouter_prefix():
    assert estimate_cost_usd("google/gemini-2.5-flash", 0, 0) == 0.0


def test_estimate_cost_unknown_model_returns_none():
    assert estimate_cost_usd("modelo-inexistente", 1000, 500) is None


@pytest.mark.asyncio
async def test_record_token_usage_persists_row(db_session, monkeypatch):
    # `record_token_usage` abre su propia sesión (async_session). En tests la
    # redirigimos a la sesión de test para poder verificar la fila.
    import app.services.ai.token_tracking as tt

    class _CtxSession:
        def __init__(self, session):
            self._session = session
        async def __aenter__(self):
            return self._session
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(tt, "async_session", lambda: _CtxSession(db_session))

    ctx = UsageContext(feature="master_brief", organization_id=None, ad_account_id=None)
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "completion_tokens_details": {"reasoning_tokens": 20},
    }
    await record_token_usage(ctx, "openai", "gpt-5.4-mini", usage)

    rows = (await db_session.execute(select(AITokenUsage))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.feature == "master_brief"
    assert row.prompt_tokens == 100
    assert row.reasoning_tokens == 20
    assert row.used_cot is True
    assert row.total_tokens == 150
    assert row.estimated_cost_usd is not None


@pytest.mark.asyncio
async def test_record_token_usage_noop_without_usage(db_session, monkeypatch):
    import app.services.ai.token_tracking as tt
    # No debe abrir sesión ni fallar si no hay usage.
    monkeypatch.setattr(tt, "async_session", lambda: (_ for _ in ()).throw(AssertionError("no debe abrir sesión")))
    await record_token_usage(UsageContext(feature="x"), "openai", "gpt-5.4", None)
    await record_token_usage(None, "openai", "gpt-5.4", {"prompt_tokens": 1})
