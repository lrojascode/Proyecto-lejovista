"""El cliente se adapta a los parámetros que cada modelo rechaza.

Este fallo ya pasó dos veces con la misma familia de modelos: primero
`max_tokens` (los de razonamiento piden `max_completion_tokens`) y después la
`temperature` distinta de la default.

Lo peligroso no es el 400: es que el 400 sea **silencioso**. El cliente devuelve
`None`, el motor cae al texto canónico, y el chat contesta con plantillas para
siempre. Nada se ve roto — sólo que no hay IA. En producción eso se leyó como
"el LLM no es capaz de listar 13 campañas".

En vez de mantener a mano una tabla de qué acepta cada modelo, se le pregunta a
la API y se recuerda la respuesta.
"""
import pytest

from app.services.ai import llm


class _Respuesta:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _ClienteFalso:
    """Rechaza los parámetros que se le indiquen, como hace la API real."""

    def __init__(self, rechaza=(), status=400):
        self.rechaza = set(rechaza)
        self.status = status
        self.cuerpos = []

    async def post(self, url, headers=None, json=None):
        self.cuerpos.append(dict(json or {}))
        for param in self.rechaza:
            if param in (json or {}):
                return _Respuesta(self.status, (
                    f"{{\"error\": {{\"message\": \"Unsupported value: '{param}' "
                    f"does not support 0.3 with this model. Only the default (1) "
                    f"is supported.\"}}}}"
                ))
        return _Respuesta(200, "{}")


@pytest.fixture(autouse=True)
def _sin_memoria_entre_tests():
    """Lo aprendido se recuerda por modelo; entre tests hay que olvidarlo."""
    llm._INCOMPATIBLES.clear()
    yield
    llm._INCOMPATIBLES.clear()


def test_reconoce_el_parametro_que_la_api_nombra():
    assert llm._param_rechazado(
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead."
    ) == "max_tokens"
    assert llm._param_rechazado(
        "Unsupported value: 'temperature' does not support 0.3 with this model."
    ) == "temperature"
    assert llm._param_rechazado("Rate limit reached") is None


@pytest.mark.asyncio
async def test_una_temperature_rechazada_no_mata_la_llamada():
    """El caso que dejó al chat contestando con plantillas."""
    cliente = _ClienteFalso(rechaza={"temperature"})

    res = await llm._post_chat(
        cliente, "http://x", {},
        {"model": "gpt-5.6-luna", "temperature": 0.3, "messages": []},
        provider="openai",
    )

    assert res.status_code == 200
    assert "temperature" not in cliente.cuerpos[-1]
    assert cliente.cuerpos[-1]["messages"] == []


@pytest.mark.asyncio
async def test_max_tokens_se_renombra_en_vez_de_perderse():
    """Sacarlo a secas dejaría la respuesta sin tope. Se renombra."""
    cliente = _ClienteFalso(rechaza={"max_tokens"})

    res = await llm._post_chat(
        cliente, "http://x", {},
        {"model": "gpt-5.6-luna", "max_tokens": 500, "messages": []},
        provider="openai",
    )

    assert res.status_code == 200
    assert cliente.cuerpos[-1]["max_completion_tokens"] == 500
    assert "max_tokens" not in cliente.cuerpos[-1]


@pytest.mark.asyncio
async def test_lo_aprendido_se_recuerda_y_no_se_repaga():
    """Se paga un reintento la primera vez, no en cada consulta del cliente."""
    cliente = _ClienteFalso(rechaza={"temperature"})
    cuerpo = {"model": "gpt-5.6-luna", "temperature": 0.3, "messages": []}

    await llm._post_chat(cliente, "http://x", {}, dict(cuerpo), provider="openai")
    primeras = len(cliente.cuerpos)

    await llm._post_chat(cliente, "http://x", {}, dict(cuerpo), provider="openai")
    assert len(cliente.cuerpos) == primeras + 1, "la segunda no debería reintentar"
    assert "temperature" not in cliente.cuerpos[-1]


@pytest.mark.asyncio
async def test_lo_aprendido_es_por_modelo():
    """Que un modelo rechace `temperature` no dice nada del otro."""
    cliente = _ClienteFalso(rechaza={"temperature"})
    await llm._post_chat(
        cliente, "http://x", {},
        {"model": "gpt-5.6-luna", "temperature": 0.3, "messages": []},
        provider="openai",
    )

    otro = _ClienteFalso()
    await llm._post_chat(
        otro, "http://x", {},
        {"model": "google/gemini-2.5-flash", "temperature": 0.3, "messages": []},
        provider="openrouter",
    )
    assert otro.cuerpos[-1]["temperature"] == 0.3


@pytest.mark.asyncio
async def test_varios_parametros_rechazados_se_van_cayendo():
    cliente = _ClienteFalso(rechaza={"temperature", "max_tokens"})

    res = await llm._post_chat(
        cliente, "http://x", {},
        {"model": "gpt-5.6-luna", "temperature": 0.3, "max_tokens": 100, "messages": []},
        provider="openai",
    )

    assert res.status_code == 200
    assert "temperature" not in cliente.cuerpos[-1]
    assert cliente.cuerpos[-1]["max_completion_tokens"] == 100


@pytest.mark.asyncio
async def test_un_400_que_no_nombra_un_parametro_no_se_reintenta():
    """Una cuota agotada o una key inválida no se arreglan sacando campos."""
    class _Cuota:
        def __init__(self):
            self.llamadas = 0

        async def post(self, url, headers=None, json=None):
            self.llamadas += 1
            return _Respuesta(400, '{"error": {"message": "Insufficient quota"}}')

    cliente = _Cuota()
    res = await llm._post_chat(
        cliente, "http://x", {}, {"model": "m", "temperature": 0.3}, provider="openai",
    )
    assert res.status_code == 400
    assert cliente.llamadas == 1


@pytest.mark.asyncio
async def test_no_se_reintenta_para_siempre():
    """Un modelo que rechaza todo no puede dejar el turno colgado."""
    class _RechazaTodo:
        def __init__(self):
            self.llamadas = 0

        async def post(self, url, headers=None, json=None):
            self.llamadas += 1
            faltante = next((k for k in ("temperature", "top_p", "seed") if k in json), None)
            if faltante is None:
                return _Respuesta(400, '{"error": {"message": "Unsupported value: \'model\' bad"}}')
            return _Respuesta(400, (
                f'{{"error": {{"message": "Unsupported value: \'{faltante}\' bad"}}}}'
            ))

    cliente = _RechazaTodo()
    res = await llm._post_chat(
        cliente, "http://x", {},
        {"model": "m", "temperature": 0.3, "top_p": 1, "seed": 1}, provider="openai",
        intentos=3,
    )
    assert res.status_code == 400
    assert cliente.llamadas == 3


# --- El 400 que dejó al analista sin consultar la base durante días ---

_ERROR_TOOLS_REASONING = (
    '{"error": {"message": "Function tools with reasoning_effort are not '
    'supported for gpt-5.6-luna in /v1/chat/completions. To use function tools, '
    'use /v1/responses or set reasoning_effort to \'none\'.", '
    '"type": "invalid_request_error", "param": "reasoning_effort", "code": null}}'
)


def test_el_parametro_sale_del_campo_param_y_no_solo_del_mensaje():
    """El regex solo reconoce "Unsupported parameter: 'X'". Este 400 nombra el
    parámetro con otras palabras —y lo trae servido en `error.param`— así que
    pasaba de largo: el analista devolvía 400 en cada mensaje del chat, caía al
    camino determinista, y parecía que el modelo no entendía las preguntas."""
    assert llm._param_rechazado(_ERROR_TOOLS_REASONING) == "reasoning_effort"


def test_el_regex_sigue_valiendo_cuando_no_hay_json():
    """Los proveedores que no devuelven `param` estructurado siguen cubiertos."""
    assert llm._param_rechazado(
        "Unsupported parameter: 'max_tokens' is not supported with this model."
    ) == "max_tokens"
    assert llm._param_rechazado("no es json ni menciona parametros") is None


@pytest.mark.asyncio
async def test_un_modelo_que_rechaza_reasoning_effort_no_pierde_las_herramientas():
    """Si algún proveedor rechaza el parámetro, se saca y se reintenta CON las
    herramientas puestas: perderlas sería volver al chat de plantillas."""
    cliente = _ClienteFalso(rechaza={"reasoning_effort"})
    cliente.cuerpo_error = _ERROR_TOOLS_REASONING

    async def _post(url, headers=None, json=None):
        cliente.cuerpos.append(dict(json or {}))
        if "reasoning_effort" in (json or {}):
            return _Respuesta(400, _ERROR_TOOLS_REASONING)
        return _Respuesta(200, "{}")

    cliente.post = _post

    res = await llm._post_chat(
        cliente, "http://x", {},
        {
            "model": "gpt-5.6-luna",
            "messages": [],
            "tools": [{"type": "function"}],
            "reasoning_effort": "none",
        },
        provider="openai",
    )

    assert res.status_code == 200
    assert "reasoning_effort" not in cliente.cuerpos[-1]
    assert cliente.cuerpos[-1]["tools"], "se perdieron las herramientas al reintentar"
