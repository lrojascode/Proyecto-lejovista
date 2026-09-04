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


def test_todo_modelo_elegible_del_catalogo_tiene_precio():
    """Un modelo sin precio reporta costo `None`, que en Uso de IA se ve como
    sin dato — y, desde la spec 10, aporta CERO a la suma que decide contra el
    presupuesto mensual. Un modelo caro sin tarifa consumiría presupuesto
    invisible para siempre.

    Antes esto se comprobaba contra `MODELS_BY_PROVIDER`, y los precios vivían
    en otro diccionario que nadie obligaba a coincidir. En el catálogo son la
    misma fila, así que se comprueba sobre la semilla completa y no solo sobre
    un proveedor."""
    from app.services.ai.catalogo import SEMILLA

    for entrada in SEMILLA:
        assert entrada.get("input"), f"{entrada['model']}: sin tarifa de entrada"
        assert entrada.get("output"), f"{entrada['model']}: sin tarifa de salida"


def test_el_catalogo_y_el_respaldo_en_codigo_no_pueden_divergir():
    """`MODELS_BY_PROVIDER` sobrevive como respaldo de `test_key`, que corre sin
    sesión de base. Que existan dos listas es el problema que el catálogo vino a
    eliminar, así que al menos tienen que decir lo mismo: sin esta comprobación,
    agregar un modelo al catálogo y olvidarlo en el respaldo devuelve el
    desalineamiento por la puerta de atrás."""
    from app.services.ai.catalogo import SEMILLA
    from app.services.ai.llm import MODELS_BY_PROVIDER

    del_catalogo = {(e["provider"], e["model"]) for e in SEMILLA}
    del_codigo = {
        (proveedor, m["value"])
        for proveedor, modelos in MODELS_BY_PROVIDER.items()
        for m in modelos
    }
    assert del_catalogo == del_codigo, (
        f"solo en el catálogo: {sorted(del_catalogo - del_codigo)}; "
        f"solo en el código: {sorted(del_codigo - del_catalogo)}"
    )


def test_las_tarifas_de_gpt_5_6():
    """Un millón de tokens de cada lado, para que el número sea legible.

    OJO: el input cacheado es ~10x más barato y el `usage` que guardamos no lo
    separa, así que todo el input se cobra a tarifa plena. La estimación queda
    POR ENCIMA del costo real, nunca por debajo.
    """
    from app.services.ai.token_tracking import estimate_cost_usd

    assert estimate_cost_usd("gpt-5.6-luna", 1_000_000, 1_000_000) == pytest.approx(1.40)
    assert estimate_cost_usd("gpt-5.6-sol", 1_000_000, 1_000_000) == pytest.approx(35.00)


def test_el_input_cacheado_se_cobra_a_su_tarifa():
    """Los `cached_tokens` vienen DENTRO de `prompt_tokens`, no aparte. Se
    restan para cobrar solo la parte no cacheada a tarifa plena."""
    from app.services.ai.token_tracking import estimate_cost_usd

    # Luna: 0.20 normal, 0.02 cacheado -- 10x de diferencia.
    assert estimate_cost_usd("gpt-5.6-luna", 1_000_000, 0, 0) == pytest.approx(0.20)
    assert estimate_cost_usd("gpt-5.6-luna", 1_000_000, 0, 1_000_000) == pytest.approx(0.02)
    # Mitad y mitad cae justo en el medio.
    assert estimate_cost_usd("gpt-5.6-luna", 1_000_000, 0, 500_000) == pytest.approx(0.11)


def test_un_modelo_sin_tarifa_de_cache_cobra_todo_a_tarifa_plena():
    """Sobrestimar es la direccion segura: este numero se usa para decidir
    cuanto gastar, y quedarse corto seria peor que pasarse."""
    from app.services.ai.token_tracking import estimate_cost_usd

    sin_cache = estimate_cost_usd("gpt-5.4-mini", 1_000_000, 0, 0)
    con_cache = estimate_cost_usd("gpt-5.4-mini", 1_000_000, 0, 1_000_000)
    assert sin_cache == con_cache == pytest.approx(0.25)


def test_mas_cacheados_que_prompt_no_baja_el_costo():
    """Si el proveedor reportara un dato incoherente, restarlo daria un input
    negativo y un costo POR DEBAJO del real."""
    from app.services.ai.token_tracking import estimate_cost_usd

    assert estimate_cost_usd("gpt-5.6-luna", 100, 0, 999_999) > 0


@pytest.mark.asyncio
async def test_se_persisten_los_tokens_cacheados_de_openai(db_session, monkeypatch):
    """OpenAI los reporta en `prompt_tokens_details.cached_tokens`."""
    import app.services.ai.token_tracking as tt

    class _CtxSession:
        def __init__(self, session):
            self._session = session
        async def __aenter__(self):
            return self._session
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(tt, "async_session", lambda: _CtxSession(db_session))

    await record_token_usage(
        UsageContext(feature="test-cache"), "openai", "gpt-5.6-luna",
        {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "total_tokens": 1100,
            "prompt_tokens_details": {"cached_tokens": 800},
        },
    )
    fila = (await db_session.execute(
        select(AITokenUsage).where(AITokenUsage.feature == "test-cache")
    )).scalar_one()

    assert fila.cached_tokens == 800
    # 200 a 0.20 + 800 a 0.02 + 100 de output a 1.20
    esperado = (200 / 1e6) * 0.20 + (800 / 1e6) * 0.02 + (100 / 1e6) * 1.20
    assert fila.estimated_cost_usd == pytest.approx(esperado, abs=1e-9)
