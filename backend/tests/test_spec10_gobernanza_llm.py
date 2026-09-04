"""Spec 10 — Gobernanza del LLM: grounding, límites, proveedores y consumo.

Los dieciocho criterios de aceptación. La frontera es la **respuesta que el
producto entrega**, no la salida cruda del modelo: se prueba con un adaptador de
proveedor controlado que devuelve las cinco salidas hostiles que la spec nombra
—cifra ausente, periodo cambiado, cuenta mezclada, presupuesto excedido y opción
fuera de catálogo— y, junto a cada una, su contracaso legítimo.

Los contracasos no son decoración. Un conjunto que solo comprueba rechazos se
aprueba endureciendo la guarda hasta que rechace todo, que es exactamente la
regresión que ya ocurrió en producción: un tope mal calibrado dejó al analista
contestando plantillas durante días, y todos los tests de grounding seguían en
verde.
"""
import datetime
import json
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ai_config_change import AIConfigChange
from app.models.ai_discard import AIDiscard
from app.models.ai_model_catalog import ESTADO_RETIRADO, AIModelCatalog
from app.models.ai_token_usage import AITokenUsage
from app.models.organization import Organization
from app.services.ai import (
    catalogo,
    compuerta,
    contrato,
    descartes,
    guardas,
    limites,
    origen,
    presupuesto,
    proveedor,
    validacion,
)
from app.services.ai.llm import OpenRouterClient


# --------------------------------------------------------------------------
# Andamiaje: un adaptador de proveedor controlado.
# --------------------------------------------------------------------------

class _Respuesta:
    def __init__(self, contenido: str, status_code: int = 200, usage=None):
        self.status_code = status_code
        self._contenido = contenido
        self.text = contenido
        self._usage = usage if usage is not None else {}

    def json(self):
        return {
            "choices": [{"message": {"content": self._contenido}}],
            "usage": self._usage,
        }


class _ProveedorControlado:
    """Devuelve lo que se le diga y anota lo que recibió.

    Es el sustituto del proveedor real en toda la spec: lo que se prueba es qué
    hace el producto CON la salida del modelo, así que la salida tiene que poder
    construirse a mano, incluidas las que ningún modelo real produciría de buena
    fe.
    """

    def __init__(self, contenido="{}", status_code=200, usage=None):
        self.contenido = contenido
        self.status_code = status_code
        self.usage = usage
        self.llamadas = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.llamadas.append({"url": url, "payload": json, "headers": headers})
        return _Respuesta(self.contenido, self.status_code, self.usage)


class _SesionPrestada:
    """Presta la sesión del test a los módulos que abren la suya.

    `token_tracking`, `descartes` y la compuerta de presupuesto usan
    `async_session` a propósito: son canales laterales que no deben quedar
    enredados con la transacción de negocio en curso. En los tests eso los deja
    escribiendo en otra sesión, y las filas no se ven. Se las redirige.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _sesiones_propias_al_motor_de_test(db_session, monkeypatch):
    import app.services.ai.compuerta as compuerta_mod
    import app.services.ai.descartes as descartes_mod
    import app.services.ai.token_tracking as tt

    prestada = _SesionPrestada(db_session)
    monkeypatch.setattr(tt, "async_session", prestada)
    monkeypatch.setattr(descartes_mod, "async_session", prestada)
    # `compuerta` la importa dentro de la función, así que se parchea en origen.
    monkeypatch.setattr("app.database.async_session", prestada)
    yield


@pytest.fixture(autouse=True)
def _sin_limite_de_frecuencia_por_defecto(monkeypatch):
    """El contador en memoria es global al proceso y sobrevive entre tests.

    Sin limpiarlo, el orden de ejecución decide si un test choca contra el tope
    puesto por otro. El test del criterio 16 lo configura explícitamente.
    """
    limites.reset_contador_local()
    yield
    limites.reset_contador_local()


def _org(**kw):
    base = dict(slug="gob", name="Gobernanza", llm_provider="openrouter",
                ai_model="google/gemini-2.5-flash")
    base.update(kw)
    return Organization(**base)


# --------------------------------------------------------------------------
# Criterio 1 — cifra ausente del conjunto de hechos: se descarta y se registra.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_una_cifra_sin_respaldo_descarta_la_respuesta_entera(db_session):
    hechos = {"gasto": 340.50, "conversaciones": 22}
    ctx = validacion.Contexto(feature="test", organization_id=None)

    veredicto = await validacion.validar(
        hechos=hechos,
        respuesta="Tu costo por conversación fue de S/ 15.48.",
        contexto=ctx,
    )

    assert veredicto.aceptada is False
    assert veredicto.motivo == "cifra_sin_respaldo"


@pytest.mark.asyncio
async def test_c1_una_cifra_copiada_de_los_datos_se_acepta(db_session):
    """Contracaso. Sin él, la guarda se aprueba rechazando todo."""
    hechos = {"gasto": 340.50, "conversaciones": 22}
    ctx = validacion.Contexto(feature="test")

    veredicto = await validacion.validar(
        hechos=hechos,
        respuesta="Gastaste S/ 340.50 y tuviste 22 conversaciones.",
        contexto=ctx,
    )

    assert veredicto.aceptada is True


@pytest.mark.asyncio
async def test_c1_el_descarte_queda_registrado_y_es_consultable(db_session: AsyncSession):
    """"Queda registrado" tiene que significar consultable, no una línea de log.

    Un `logger.warning` se rota y se pierde: no se puede responder cuántas
    respuestas se descartaron la semana pasada ni por qué motivo, que es
    precisamente lo que hace falta para detectar que un prompt se rompió.
    """
    await validacion.validar(
        hechos={"gasto": 10.0},
        respuesta="El total fue S/ 9999.00.",
        contexto=validacion.Contexto(
            feature="campaign_chat", provider="openrouter",
            model="google/gemini-2.5-flash", prompt_version="v-de-prueba",
        ),
    )

    filas = (await db_session.execute(select(AIDiscard))).scalars().all()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.motivo == "cifra_sin_respaldo"
    assert fila.feature == "campaign_chat"
    assert fila.prompt_version == "v-de-prueba"
    assert fila.aplicado is True


# --------------------------------------------------------------------------
# Criterio 2 — periodo cambiado.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c2_cambiar_el_mes_descarta_aunque_las_cifras_esten_respaldadas(db_session):
    """El fallo que la guarda numérica no puede ver: un mes se escribe con letras.

    Todas las cifras existen en los datos. Lo único falso es el mes, y eso basta
    para que la respuesta describa un periodo que nadie consultó.
    """
    periodo = guardas.PeriodoAutorizado(
        desde=datetime.date(2026, 8, 1), hasta=datetime.date(2026, 8, 31), dias=30,
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 1240.00},
        respuesta="En julio gastaste S/ 1240.00 en total.",
        contexto=validacion.Contexto(feature="test", periodo=periodo),
    )

    assert veredicto.aceptada is False
    assert veredicto.motivo == "periodo_cambiado"
    assert "julio" in veredicto.detalle


@pytest.mark.asyncio
async def test_c2_el_mes_correcto_se_acepta(db_session):
    periodo = guardas.PeriodoAutorizado(
        desde=datetime.date(2026, 8, 1), hasta=datetime.date(2026, 8, 31), dias=30,
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 1240.00},
        respuesta="En agosto gastaste S/ 1240.00 en total.",
        contexto=validacion.Contexto(feature="test", periodo=periodo),
    )
    assert veredicto.aceptada is True


@pytest.mark.asyncio
async def test_c2_cambiar_la_ventana_de_dias_descarta(db_session):
    """Se consultaron 7 días y la respuesta dice 30.

    El 30 puede estar respaldado por cualquier otra cifra de los datos —acá lo
    está por el gasto—, así que la guarda numérica lo deja pasar. Es exactamente
    el motivo por el que el periodo necesita su propia comprobación.
    """
    periodo = guardas.PeriodoAutorizado(
        desde=datetime.date(2026, 8, 25), hasta=datetime.date(2026, 8, 31), dias=7,
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 30.00, "conversaciones": 7},
        respuesta="En los últimos 30 días gastaste S/ 30.00.",
        contexto=validacion.Contexto(feature="test", periodo=periodo),
    )

    assert veredicto.aceptada is False
    assert veredicto.motivo == "periodo_cambiado"


@pytest.mark.asyncio
async def test_c2_la_ventana_correcta_se_acepta(db_session):
    periodo = guardas.PeriodoAutorizado(
        desde=datetime.date(2026, 8, 25), hasta=datetime.date(2026, 8, 31), dias=7,
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 30.00, "conversaciones": 7},
        respuesta="En los últimos 7 días gastaste S/ 30.00.",
        contexto=validacion.Contexto(feature="test", periodo=periodo),
    )
    assert veredicto.aceptada is True


# --------------------------------------------------------------------------
# Criterio 3 — cuenta distinta de la activa.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c3_nombrar_una_cuenta_hermana_descarta(db_session):
    """Todas las cifras correctas y la conclusión falsa.

    Es el fallo más difícil de ver desde fuera: los números existen, solo que en
    otra cuenta de la misma Organización.
    """
    cuenta = guardas.CuentaAutorizada(
        external_id="act_111222333",
        nombre="Clinica Sonrisa Lima",
        otros_nombres=("Clinica Sonrisa Arequipa",),
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 890.00},
        respuesta="Clinica Sonrisa Arequipa gastó S/ 890.00 este periodo.",
        contexto=validacion.Contexto(feature="test", cuenta=cuenta),
    )

    assert veredicto.aceptada is False
    assert veredicto.motivo == "cuenta_ajena"


@pytest.mark.asyncio
async def test_c3_nombrar_la_cuenta_propia_se_acepta(db_session):
    cuenta = guardas.CuentaAutorizada(
        external_id="act_111222333",
        nombre="Clinica Sonrisa Lima",
        otros_nombres=("Clinica Sonrisa Arequipa",),
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 890.00},
        respuesta="Clinica Sonrisa Lima gastó S/ 890.00 este periodo.",
        contexto=validacion.Contexto(feature="test", cuenta=cuenta),
    )
    assert veredicto.aceptada is True


@pytest.mark.asyncio
async def test_c3_un_identificador_de_otra_cuenta_descarta(db_session):
    cuenta = guardas.CuentaAutorizada(
        external_id="act_111222333", nombre="Cuenta Activa",
        otras_ids=("act_444555666",),
    )
    veredicto = await validacion.validar(
        hechos={"gasto_total": 890.00},
        respuesta="La cuenta act_444555666 gastó S/ 890.00.",
        contexto=validacion.Contexto(feature="test", cuenta=cuenta),
    )
    assert veredicto.aceptada is False
    assert veredicto.motivo == "cuenta_ajena"


def test_c3_un_nombre_contenido_en_el_propio_no_dispara_falso_positivo():
    """"Kibo" dentro de "Kibo Perú" no prueba que la respuesta se haya ido.

    Sin esta salvedad, una Organización con cuentas de nombres emparentados
    —lo normal: "Marca Lima", "Marca Arequipa"— vería descartada cualquier
    respuesta que nombre su propia cuenta.
    """
    cuenta = guardas.CuentaAutorizada(
        external_id="act_1", nombre="Kibo Perú", otros_nombres=("Kibo",),
    )
    assert guardas.cuenta_incoherente(cuenta, "Kibo Perú gastó S/ 100.00") is None


# --------------------------------------------------------------------------
# Criterio 4 — opción fuera de catálogo cerrado.
# --------------------------------------------------------------------------

def test_c4_un_valor_fuera_del_catalogo_rompe_el_contrato():
    c = contrato.ContratoDeSalida(
        nombre="objetivo",
        campos=(contrato.Campo(
            nombre="objetivo",
            catalogo=("OUTCOME_LEADS", "OUTCOME_ENGAGEMENT", "OUTCOME_TRAFFIC"),
        ),),
    )
    with pytest.raises(contrato.ContratoIncumplido) as e:
        c.validar({"objetivo": "LEADS"})
    assert "fuera del catálogo" in str(e.value)


def test_c4_no_se_corrige_lo_que_casi_encaja():
    """"LEADS" se parece muchísimo a "OUTCOME_LEADS", y aun así se descarta.

    Un corrector de "casi encaja" es una elección que nadie tomó, hecha por
    código que no sabe qué quiso decir el modelo, sobre un campo que termina en
    una campaña con dinero real.
    """
    c = contrato.ContratoDeSalida(
        nombre="objetivo",
        campos=(contrato.Campo(nombre="objetivo", catalogo=("OUTCOME_LEADS",)),),
    )
    assert c.cumple({"objetivo": "LEADS"}) is False
    assert c.cumple({"objetivo": "OUTCOME_LEADS"}) is True


def test_c4_un_campo_obligatorio_ausente_no_toma_su_default():
    """Un default silencioso convierte "el modelo no supo" en "el modelo eligió"."""
    c = contrato.ContratoDeSalida(
        nombre="objetivo",
        campos=(contrato.Campo(nombre="objetivo", catalogo=("OUTCOME_LEADS",)),),
    )
    motivos = c.motivos({})
    assert motivos and "obligatorio" in motivos[0]


def test_c4_el_contrato_devuelve_solo_los_campos_declarados():
    """Tolerar un campo extra no lo convierte en dato de confianza."""
    c = contrato.ContratoDeSalida(
        nombre="x", campos=(contrato.Campo(nombre="objetivo", catalogo=("A",)),),
    )
    limpio = c.validar({"objetivo": "A", "razonamiento": "porque sí"})
    assert limpio == {"objetivo": "A"}


def test_c4_el_periodo_solo_puede_salir_del_enum_cerrado():
    """El enum de periodos existe para que el modelo no se acerque a inventar días."""
    c = contrato.ContratoDeSalida(
        nombre="plan",
        campos=(contrato.Campo(
            nombre="periodo", catalogo=("7d", "30d", "90d", "180d", "all"),
        ),),
    )
    assert c.cumple({"periodo": "37d"}) is False
    assert c.cumple({"periodo": "30d"}) is True


# --------------------------------------------------------------------------
# Criterio 5 — nunca se promete éxito por la apariencia.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "Esta imagen va a funcionar muy bien.",
    "Con esta foto vas a conseguir muchas más consultas.",
    "Te garantizo que esta creatividad será un éxito.",
    "Esta pieza seguro convierte.",
])
def test_c5_las_promesas_de_exito_visual_se_detectan(texto):
    assert guardas.promesa_de_exito(texto) is not None


@pytest.mark.parametrize("texto", [
    "Esta pieza se parece a la de Verano, que consiguió conversaciones a S/ 15.48.",
    "Vale la pena probarla y medir cómo rinde.",
    "La campaña Verano va a seguir activa hasta el viernes.",
])
def test_c5_el_parecido_medido_y_la_recomendacion_de_probar_son_legitimos(texto):
    """El sistema PUEDE decir que algo se parece a lo que rindió bien: es un
    hecho medido. Lo que no puede es afirmar que va a funcionar."""
    assert guardas.promesa_de_exito(texto) is None


@pytest.mark.asyncio
async def test_c5_una_promesa_descarta_la_respuesta(db_session):
    veredicto = await validacion.validar(
        hechos={},
        respuesta="Esta imagen va a funcionar, tiene todo lo de un anuncio ganador.",
        contexto=validacion.Contexto(feature="campaign_copy"),
    )
    assert veredicto.aceptada is False
    assert veredicto.motivo == "promesa_de_exito"


# --------------------------------------------------------------------------
# Criterio 6 — nada simulado se presenta como análisis real en producción.
# --------------------------------------------------------------------------

def test_c6_en_produccion_el_contenido_simulado_no_puede_salir(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)

    with pytest.raises(origen.ContenidoSimuladoEnProduccion):
        origen.marcar({"type": "text"}, origen.ORIGEN_SIMULADO)


def test_c6_en_desarrollo_el_contenido_simulado_sale_marcado(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)

    salida = origen.marcar({"type": "text"}, origen.ORIGEN_SIMULADO)
    assert salida["origen"] == "simulado"
    assert "SIMULADO" in salida["origen_label"]


def test_c6_la_caida_a_texto_determinista_se_identifica_en_produccion(monkeypatch):
    """Una respuesta determinista SÍ puede salir en producción: sus cifras vienen
    de los datos del cliente igual que las otras. Lo que no puede es pasar por
    redacción del modelo sin decirlo."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)

    salida = origen.marcar({"type": "text"}, origen.ORIGEN_DETERMINISTA)
    assert salida["origen"] == "determinista"
    assert salida["origen_label"]

    assert origen.origen_de_respaldo("chat") == origen.ORIGEN_DETERMINISTA


def test_c6_una_respuesta_del_modelo_no_lleva_etiqueta(monkeypatch):
    """Que el modelo redacte es lo esperado; anunciarlo en cada mensaje es ruido."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    salida = origen.marcar({"type": "text"}, origen.ORIGEN_MODELO)
    assert "origen_label" not in salida


# --------------------------------------------------------------------------
# Criterio 7 — cada llamada real deja su ficha completa.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_la_llamada_registra_proveedor_modelo_prompt_uso_y_costo(
    db_session: AsyncSession, monkeypatch,
):
    from app.config import settings
    from app.services.ai.token_tracking import UsageContext

    org = _org()
    db_session.add(org)
    await db_session.commit()

    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-key")
    falso = _ProveedorControlado(
        contenido=json.dumps({"text": "Gastaste S/ 340.50."}),
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    )
    monkeypatch.setattr(httpx, "AsyncClient", falso)

    await OpenRouterClient(organization=org).compose_chat_answer(
        org, "cuánto gasté", "SPEND", {"gasto": 340.50},
        usage_ctx=UsageContext(feature="campaign_chat", organization_id=org.id),
    )

    filas = (await db_session.execute(select(AITokenUsage))).scalars().all()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.provider == "openrouter"
    assert fila.model == "gemini-2.5-flash"
    assert fila.prompt_version == "campaign-chat-answer-v1"
    assert fila.prompt_tokens == 1000 and fila.completion_tokens == 500
    # Costo desde el CATÁLOGO, no desde un diccionario del código.
    assert fila.estimated_cost_usd is not None
    assert fila.estimated_cost_usd_exact is not None
    # gemini-2.5-flash: 0.15/1M input + 0.60/1M output.
    assert fila.estimated_cost_usd_exact == Decimal("0.000450")


@pytest.mark.asyncio
async def test_c7_un_modelo_sin_tarifa_deja_el_costo_sin_estimar_y_no_en_cero(
    db_session: AsyncSession, monkeypatch,
):
    """`None` no es `0`. Un cero mentiría en el panel de gasto y, peor, en la
    suma que decide contra el presupuesto: un modelo sin tarifa consumiría
    presupuesto invisible para siempre."""
    from app.config import settings
    from app.services.ai.token_tracking import UsageContext

    org = _org(ai_model="modelo-que-no-existe")
    db_session.add(org)
    await db_session.commit()

    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(httpx, "AsyncClient", _ProveedorControlado(
        contenido=json.dumps({"text": "ok"}),
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    ))

    await OpenRouterClient(organization=org).compose_chat_answer(
        org, "x", "SPEND", {"gasto": 1.0},
        usage_ctx=UsageContext(feature="campaign_chat", organization_id=org.id),
    )

    fila = (await db_session.execute(select(AITokenUsage))).scalars().one()
    assert fila.estimated_cost_usd is None
    assert fila.estimated_cost_usd_exact is None


# --------------------------------------------------------------------------
# Criterios 8 y 9 — sin cambios silenciosos de proveedor.
# --------------------------------------------------------------------------

def test_c8_sin_la_clave_del_proveedor_declarado_se_falla_de_forma_visible(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "gm-key")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-key")

    with pytest.raises(proveedor.CredencialAusente) as e:
        proveedor.resolver(_org(llm_provider="openai"))

    # El mensaje tiene que nombrar el proveedor que falta y decir qué hacer.
    assert "openai" in str(e.value)
    assert "no se usa la credencial de otro proveedor" in str(e.value).lower()


def test_c9_anadir_la_clave_de_otro_proveedor_no_mueve_a_nadie(monkeypatch):
    """El cambio silencioso más caro que tenía el sistema.

    Añadir `OPENAI_API_KEY` al entorno movía a TODAS las organizaciones sin
    clave propia de OpenRouter a OpenAI en el siguiente reinicio: otro modelo,
    otro precio, otro comportamiento, sin registro.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-key")

    org = _org(llm_provider="openrouter")
    antes = proveedor.resolver(org)

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-nueva")
    despues = proveedor.resolver(org)

    assert antes.provider == despues.provider == "openrouter"
    assert antes.api_key == despues.api_key == "or-key"


def test_c9_rotar_la_credencial_por_una_de_otra_forma_no_cambia_el_proveedor():
    """Antes el proveedor se deducía de la FORMA de la clave, así que guardar
    una `sk-` en una organización de OpenRouter la mudaba a OpenAI."""
    org = _org(llm_provider="openrouter")
    org.set_llm_api_key("sk-proj-parece-de-openai")

    cfg = proveedor.resolver(org)

    assert cfg.provider == "openrouter"
    assert "openrouter.ai" in cfg.base_url


def test_c8_sin_proveedor_declarado_no_se_adivina():
    with pytest.raises(proveedor.ProveedorNoConfigurado):
        proveedor.resolver(_org(llm_provider=None))


# --------------------------------------------------------------------------
# Criterio 10 — el cambio queda registrado con actor y momento.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c10_cambiar_el_modelo_deja_actor_y_momento(db_session: AsyncSession):
    from app.models.user import User

    org = _org()
    db_session.add(org)
    # `actor_user_id` es una clave foránea: quién cambió el modelo tiene que ser
    # alguien. Un identificador inventado pasa contra el motor embebido, que no
    # comprueba las claves foráneas por defecto, y rompe contra PostgreSQL.
    actor = User(email="agencia@ejemplo.com", hashed_password="x", full_name="Agencia")
    db_session.add(actor)
    await db_session.commit()

    await proveedor.registrar_cambio(
        db_session, org, campo="ai_model",
        valor_anterior="google/gemini-2.5-flash", valor_nuevo="google/gemini-2.5-pro",
        actor_user_id=actor.id, actor_email="agencia@ejemplo.com",
    )
    await db_session.commit()

    fila = (await db_session.execute(select(AIConfigChange))).scalars().one()
    assert fila.campo == "ai_model"
    assert fila.valor_anterior == "google/gemini-2.5-flash"
    assert fila.valor_nuevo == "google/gemini-2.5-pro"
    assert fila.actor_email == "agencia@ejemplo.com"
    assert fila.created_at is not None


@pytest.mark.asyncio
async def test_c10_un_cambio_que_no_cambia_nada_no_se_registra(db_session: AsyncSession):
    """Llenar la bitácora de filas donde el valor anterior y el nuevo coinciden
    hace más difícil encontrar el cambio que sí importó."""
    org = _org()
    db_session.add(org)
    await db_session.commit()

    resultado = await proveedor.registrar_cambio(
        db_session, org, campo="ai_model", valor_anterior="a", valor_nuevo="a",
    )
    await db_session.commit()

    assert resultado is None
    assert (await db_session.execute(select(AIConfigChange))).scalars().all() == []


@pytest.mark.asyncio
async def test_c10_la_credencial_se_registra_como_hecho_nunca_su_valor(db_session: AsyncSession):
    org = _org()
    db_session.add(org)
    await db_session.commit()

    await proveedor.registrar_cambio(
        db_session, org, campo="llm_api_key",
        valor_anterior="sk-vieja-secreta", valor_nuevo="sk-nueva-secreta",
    )
    await db_session.commit()

    fila = (await db_session.execute(select(AIConfigChange))).scalars().one()
    assert "secreta" not in (fila.valor_anterior or "")
    assert "secreta" not in (fila.valor_nuevo or "")
    assert fila.valor_nuevo == "(credencial nueva)"


# --------------------------------------------------------------------------
# Criterios 11 y 12 — catálogo: qué se ofrece y qué pasa al retirar.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c11_el_selector_solo_ofrece_modelos_del_proveedor_activo(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": "cat@ejemplo.com", "password": "securepassword123",
        "full_name": "Cat", "organization_slug": "cat-org", "organization_name": "Cat",
    })
    headers = {"Authorization": f"Bearer {alta.json()['access_token']}"}

    res = await async_client.get("/api/v1/organizations/ai-models", headers=headers)
    cuerpo = res.json()

    assert cuerpo["active_provider"] == "openai"
    valores = [m["value"] for m in cuerpo["models"]]
    assert valores and all(not v.startswith("google/") for v in valores)
    assert "gemini-2.5-pro" not in valores


@pytest.mark.asyncio
async def test_c11_un_modelo_retirado_deja_de_ofrecerse(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """Retirar es un cambio de DATO: no hace falta desplegar código."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": "ret@ejemplo.com", "password": "securepassword123",
        "full_name": "Ret", "organization_slug": "ret-org", "organization_name": "Ret",
    })
    headers = {"Authorization": f"Bearer {alta.json()['access_token']}"}

    antes = (await async_client.get("/api/v1/organizations/ai-models", headers=headers)).json()
    assert "gpt-5.4-nano" in [m["value"] for m in antes["models"]]

    await catalogo.retirar(db_session, "openai", "gpt-5.4-nano",
                           motivo="lo retiró el proveedor", definitivo=True)
    await db_session.commit()

    despues = (await async_client.get("/api/v1/organizations/ai-models", headers=headers)).json()
    assert "gpt-5.4-nano" not in [m["value"] for m in despues["models"]]


@pytest.mark.asyncio
async def test_c12_retirar_senala_a_quien_lo_usaba_sin_reasignarlo(db_session: AsyncSession):
    """Reasignar automáticamente sería el cambio silencioso de modelo —y de
    precio— que el criterio 9 prohíbe. El modelo nuevo puede costar un múltiplo
    del anterior."""
    org = _org(llm_provider="openai", ai_model="gpt-5.4-nano")
    db_session.add(org)
    await db_session.commit()

    afectadas = await catalogo.retirar(
        db_session, "openai", "gpt-5.4-nano", motivo="retirado por el proveedor",
        definitivo=True,
    )
    await db_session.commit()
    await db_session.refresh(org)

    assert [o.slug for o in afectadas] == ["gob"]
    # No se tocó: sigue apuntando al modelo retirado, señalada y funcionando.
    assert org.ai_model == "gpt-5.4-nano"

    señaladas = await catalogo.organizaciones_con_modelo_no_elegible(db_session)
    assert señaladas and señaladas[0]["slug"] == "gob"
    assert señaladas[0]["estado"] == ESTADO_RETIRADO


@pytest.mark.asyncio
async def test_c12_la_pantalla_avisa_de_que_el_modelo_ya_no_es_elegible(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """"Sin romperse en silencio" tiene que llegar hasta la pantalla."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": "sen@ejemplo.com", "password": "securepassword123",
        "full_name": "Sen", "organization_slug": "sen-org", "organization_name": "Sen",
    })
    headers = {"Authorization": f"Bearer {alta.json()['access_token']}"}

    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "sen-org")
    )).scalar_one()
    org.ai_model = "gpt-5.4-nano"
    await db_session.commit()

    await catalogo.retirar(db_session, "openai", "gpt-5.4-nano",
                           motivo="lo retiró el proveedor", definitivo=True)
    await db_session.commit()

    cuerpo = (await async_client.get("/api/v1/organizations/ai-models", headers=headers)).json()
    assert cuerpo["modelo_no_elegible"] is True
    assert "retiró el proveedor" in cuerpo["modelo_no_elegible_motivo"]


@pytest.mark.asyncio
async def test_c11_sembrar_es_idempotente_y_no_pisa_ediciones(db_session: AsyncSession):
    """Un precio corregido a mano tiene que sobrevivir al reinicio."""
    fila = (await db_session.execute(
        select(AIModelCatalog).where(AIModelCatalog.model == "gpt-5.6-luna")
    )).scalar_one()
    fila.precio_input = Decimal("9.99")
    await db_session.commit()

    nuevos = await catalogo.sembrar(db_session)
    await db_session.commit()
    await db_session.refresh(fila)

    assert nuevos == 0
    assert fila.precio_input == Decimal("9.990000")


# --------------------------------------------------------------------------
# Criterios 13, 14, 15 y 17 — presupuesto de IA.
# --------------------------------------------------------------------------

async def _gastar(db: AsyncSession, org_id: str, usd: str, cuando=None):
    db.add(AITokenUsage(
        organization_id=org_id, feature="campaign_chat", provider="openrouter",
        model="gemini-2.5-flash", prompt_tokens=1, completion_tokens=1, total_tokens=2,
        estimated_cost_usd=float(usd), estimated_cost_usd_exact=Decimal(usd),
        created_at=cuando or datetime.datetime.utcnow(),
    ))
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("gastado,umbral", [
    ("6.99", None),
    ("7.00", 70),
    ("8.99", 70),
    ("9.00", 90),
    ("9.99", 90),
    ("10.00", 100),
])
async def test_c13_los_umbrales_se_cruzan_donde_dice_la_spec(
    db_session: AsyncSession, gastado, umbral,
):
    """Los bordes exactos. Un umbral que se dispara un centavo antes o después
    no es un detalle: al 100 % decide un bloqueo."""
    org = _org(ai_monthly_budget_usd=Decimal("10.00"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, gastado)

    estado = await presupuesto.estado_del_mes(db_session, org.id, org=org)
    assert estado.umbral_alcanzado == umbral


@pytest.mark.asyncio
async def test_c13_la_alerta_se_emite_una_sola_vez_por_umbral(db_session: AsyncSession, mocker):
    """Sin esto, el aviso saldría en cada llamada al modelo a partir del 70 %:
    cientos de correos en un día de uso normal, y el efecto práctico es que la
    agencia deja de leerlos."""
    from app.models.organization_user import OrganizationUser
    from app.models.user import User

    org = _org(ai_monthly_budget_usd=Decimal("10.00"))
    db_session.add(org)
    await db_session.flush()
    usuario = User(email="admin@ejemplo.com", hashed_password="x", full_name="Admin")
    db_session.add(usuario)
    await db_session.flush()
    db_session.add(OrganizationUser(user_id=usuario.id, organization_id=org.id, role="ADMIN"))
    await db_session.commit()
    await _gastar(db_session, org.id, "7.50")

    enviar = mocker.patch("app.services.ai.compuerta.send_email", return_value=True)

    estado = await presupuesto.estado_del_mes(db_session, org.id, org=org)
    primero = await compuerta.emitir_alerta_si_toca(db_session, org, estado)
    segundo = await compuerta.emitir_alerta_si_toca(db_session, org, estado)

    assert primero == 70
    assert segundo is None
    assert enviar.await_count == 1


@pytest.mark.asyncio
async def test_c13_cruzar_el_siguiente_umbral_vuelve_a_avisar(db_session: AsyncSession, mocker):
    """"Una vez" es por umbral, no por mes: el 90 % tiene que avisar aunque ya
    se haya avisado el 70 %."""
    org = _org(ai_monthly_budget_usd=Decimal("10.00"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "7.50")
    mocker.patch("app.services.ai.compuerta.send_email", return_value=True)

    estado = await presupuesto.estado_del_mes(db_session, org.id, org=org)
    assert await compuerta.emitir_alerta_si_toca(db_session, org, estado) == 70

    await _gastar(db_session, org.id, "1.60")  # total 9.10 → 91 %
    estado = await presupuesto.estado_del_mes(db_session, org.id, org=org)
    assert await compuerta.emitir_alerta_si_toca(db_session, org, estado) == 90


@pytest.mark.asyncio
async def test_c14_al_100_por_ciento_se_bloquean_las_llamadas(db_session: AsyncSession):
    org = _org(ai_monthly_budget_usd=Decimal("10.00"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "10.00")

    with pytest.raises(presupuesto.PresupuestoDeIAAgotado) as e:
        await presupuesto.assert_hay_presupuesto(db_session, org)

    detalle = e.value.as_detail()
    assert detalle["code"] == "AI_BUDGET_EXHAUSTED"
    # Honestidad: se nombra lo que SIGUE funcionando.
    assert "métricas" in detalle["message"]


@pytest.mark.asyncio
async def test_c14_el_bloqueo_es_de_backend_y_no_lo_evita_la_superficie(
    db_session: AsyncSession, monkeypatch,
):
    """La compuerta vive en el punto por el que pasa toda llamada real al
    modelo, no en cada superficie. Una superficie nueva queda cubierta por
    construcción, no por acordarse."""
    from app.config import settings

    org = _org(ai_monthly_budget_usd=Decimal("1.00"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "1.00")

    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-key")
    falso = _ProveedorControlado(contenido=json.dumps({"text": "hola"}))
    monkeypatch.setattr(httpx, "AsyncClient", falso)

    # `compose_chat_answer` traga sus excepciones y devuelve None: el chat cae al
    # camino determinista. Lo que se comprueba es que NO se llamó al proveedor.
    await OpenRouterClient(organization=org).compose_chat_answer(
        org, "cuánto gasté", "SPEND", {"gasto": 1.0},
    )

    assert falso.llamadas == [], "se llamó al modelo con el presupuesto agotado"


@pytest.mark.asyncio
async def test_c15_con_el_presupuesto_agotado_el_chat_sigue_respondiendo(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """El cliente pierde el redactor, no el tablero."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openrouter")

    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": "pres@ejemplo.com", "password": "securepassword123",
        "full_name": "Pres", "organization_slug": "pres-org", "organization_name": "Pres",
    })
    headers = {"Authorization": f"Bearer {alta.json()['access_token']}"}

    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "pres-org")
    )).scalar_one()
    org.ai_monthly_budget_usd = Decimal("1.00")
    await db_session.commit()
    await _gastar(db_session, org.id, "1.00")

    # El tablero determinista sigue en pie.
    res = await async_client.get("/api/v1/organizations/my", headers=headers)
    assert res.status_code == 200

    estado = await presupuesto.estado_del_mes(db_session, org.id, org=org)
    assert estado.agotado is True


@pytest.mark.asyncio
async def test_c17_el_presupuesto_se_reinicia_al_empezar_el_mes(db_session: AsyncSession):
    """Con reloj controlado, no esperando a que cambie el mes."""
    org = _org(ai_monthly_budget_usd=Decimal("10.00"))
    db_session.add(org)
    await db_session.commit()

    mes_pasado = datetime.datetime(2026, 7, 20, 12, 0)
    await _gastar(db_session, org.id, "10.00", cuando=mes_pasado)

    # Mirando desde julio: agotado.
    en_julio = await presupuesto.estado_del_mes(
        db_session, org.id, org=org, momento=datetime.datetime(2026, 7, 25, 12, 0),
    )
    assert en_julio.agotado is True

    # Mirando desde agosto: el consumo de julio no cuenta.
    en_agosto = await presupuesto.estado_del_mes(
        db_session, org.id, org=org, momento=datetime.datetime(2026, 8, 2, 12, 0),
    )
    assert en_agosto.consumo == Decimal("0.000000")
    assert en_agosto.agotado is False


@pytest.mark.asyncio
async def test_c17_la_marca_de_aviso_no_sobrevive_al_cambio_de_mes(db_session: AsyncSession):
    """El reinicio de los avisos sale de la marca: al cambiar el mes cambia el
    prefijo y los tres umbrales vuelven a estar disponibles."""
    org = _org(ai_monthly_budget_usd=Decimal("10.00"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "7.50", cuando=datetime.datetime(2026, 7, 10))

    julio = await presupuesto.estado_del_mes(
        db_session, org.id, org=org, momento=datetime.datetime(2026, 7, 20),
    )
    assert presupuesto.aviso_pendiente(org, julio) == 70
    presupuesto.marcar_avisado(org, julio, 70)
    assert presupuesto.aviso_pendiente(org, julio) is None

    await _gastar(db_session, org.id, "7.50", cuando=datetime.datetime(2026, 8, 10))
    agosto = await presupuesto.estado_del_mes(
        db_session, org.id, org=org, momento=datetime.datetime(2026, 8, 20),
    )
    assert presupuesto.aviso_pendiente(org, agosto) == 70


@pytest.mark.asyncio
async def test_c14_sin_techo_configurado_no_se_bloquea_a_nadie(db_session: AsyncSession):
    """Es el estado de toda organización anterior a esta spec. Bloquear
    retroactivamente a quien nunca acordó un límite le cortaría el producto de
    un despliegue al otro."""
    org = _org(ai_monthly_budget_usd=None)
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "9999.00")

    estado = await presupuesto.assert_hay_presupuesto(db_session, org)
    assert estado.sin_techo is True
    assert estado.agotado is False


@pytest.mark.asyncio
async def test_c14_un_techo_de_cero_es_sin_ia_no_sin_techo(db_session: AsyncSession):
    """Confundirlos dejaría la IA encendida cuando alguien la apagó poniendo 0."""
    org = _org(ai_monthly_budget_usd=Decimal("0"))
    db_session.add(org)
    await db_session.commit()

    with pytest.raises(presupuesto.PresupuestoDeIAAgotado):
        await presupuesto.assert_hay_presupuesto(db_session, org)


@pytest.mark.asyncio
async def test_c14_el_consumo_se_suma_en_decimal_y_no_en_coma_flotante(db_session: AsyncSession):
    """Tres décimas de dólar que en float no suman exactamente su total.

    `0.1 + 0.2 != 0.3` en binario. Sobre una compuerta que decide si se llama al
    modelo, ese residuo autoriza o bloquea por un motivo que no es el límite.
    """
    org = _org(ai_monthly_budget_usd=Decimal("0.30"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "0.10")
    await _gastar(db_session, org.id, "0.20")

    estado = await presupuesto.estado_del_mes(db_session, org.id, org=org)

    assert estado.consumo == Decimal("0.300000")
    assert estado.agotado is True, "el borde exacto tiene que agotar"


# --------------------------------------------------------------------------
# Criterio 16 — reintentos y frecuencia acotados.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c16_el_limite_de_frecuencia_corta_el_bucle(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "AI_MAX_LLAMADAS_POR_MINUTO", 3)
    limites.reset_contador_local()

    for _ in range(3):
        await limites.assert_frecuencia("org-bucle")

    with pytest.raises(limites.LimiteDeFrecuencia) as e:
        await limites.assert_frecuencia("org-bucle")

    assert e.value.limite == 3
    assert e.value.as_detail()["code"] == "AI_RATE_LIMITED"


@pytest.mark.asyncio
async def test_c16_el_contador_local_sigue_en_pie_sin_redis(monkeypatch):
    """Fail-open a secas dejaría el bucle completamente suelto justo cuando la
    infraestructura está en problemas. El contador en memoria no ve a los otros
    trabajadores, pero acota el caso que importa: un proceso girando en falso.

    La suite ya apunta REDIS_URL a un puerto muerto, así que este test corre
    contra la caída real, no contra una simulada.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "AI_MAX_LLAMADAS_POR_MINUTO", 2)
    limites.reset_contador_local()

    await limites.assert_frecuencia("org-sin-redis")
    await limites.assert_frecuencia("org-sin-redis")

    with pytest.raises(limites.LimiteDeFrecuencia) as e:
        await limites.assert_frecuencia("org-sin-redis")
    assert e.value.alcance == "proceso"


@pytest.mark.asyncio
async def test_c16_el_limite_cero_significa_sin_limite(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "AI_MAX_LLAMADAS_POR_MINUTO", 0)
    limites.reset_contador_local()
    for _ in range(50):
        await limites.assert_frecuencia("org-libre")


@pytest.mark.asyncio
async def test_c16_los_reintentos_tienen_tope_configurable(monkeypatch):
    """El 400 que nombra un parámetro se reintenta adaptando el cuerpo. Ese
    bucle tiene que acabar: un modelo que rechaza un parámetro tras otro
    consumiría el presupuesto del cliente reintentando."""
    from app.config import settings
    from app.services.ai.llm import _post_chat

    monkeypatch.setattr(settings, "AI_MAX_REINTENTOS", 1)
    monkeypatch.setattr(limites, "espera_de_reintento", lambda intento: 0.0)

    class _Siempre400:
        def __init__(self):
            self.intentos = 0

        async def post(self, url, headers=None, json=None):
            self.intentos += 1
            return _Respuesta(
                json_error := '{"error": {"param": "temperature", "message": "no"}}',
                status_code=400,
            )

    cliente = _Siempre400()
    await _post_chat(
        cliente, "http://x", {}, {"model": "m", "temperature": 0.2}, provider="openai",
    )

    # 1 reintento configurado = 2 intentos como mucho.
    assert cliente.intentos <= 2


def test_c16_el_retroceso_crece_entre_reintentos():
    """Sin retroceso, el reintento castiga al proveedor que ya está en problemas."""
    assert limites.espera_de_reintento(0) == 0.0
    assert limites.espera_de_reintento(1) < limites.espera_de_reintento(2)


# --------------------------------------------------------------------------
# Criterio 18 — conjunto de evaluación.
# --------------------------------------------------------------------------

def test_c18_el_conjunto_de_evaluacion_existe_y_esta_versionado():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1] / "evaluacion"
    assert (raiz / "README.md").exists()

    casos = list((raiz / "casos").glob("*.json"))
    assert casos, "el conjunto de evaluación no puede estar vacío"


def test_c18_el_conjunto_tiene_casos_hostiles_y_legitimos():
    """Un conjunto solo de casos hostiles se aprueba endureciendo la guarda
    hasta que rechace todo, que es la regresión que ya ocurrió. Los casos
    legítimos son los que hacen que "más estricto" deje de ser gratis."""
    import json as json_mod
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1] / "evaluacion" / "casos"
    esperas = []
    for archivo in raiz.glob("*.json"):
        esperas += [c["espera"] for c in json_mod.loads(archivo.read_text())]

    assert "descartar" in esperas
    assert "aceptar" in esperas
    assert esperas.count("aceptar") >= 4, "hacen falta suficientes contracasos"


def test_c18_cada_caso_declara_su_dimension():
    import json as json_mod
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1] / "evaluacion" / "casos"
    for archivo in raiz.glob("*.json"):
        for caso in json_mod.loads(archivo.read_text()):
            assert caso.get("id"), f"{archivo.name}: un caso sin id"
            assert caso.get("dimension") in ("grounding", "seguridad", "costo"), caso["id"]
            assert caso.get("espera") in ("descartar", "aceptar"), caso["id"]


# --------------------------------------------------------------------------
# Modo de registro: la activación que pide la spec.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_el_modo_de_registro_anota_pero_no_descarta(db_session: AsyncSession, monkeypatch):
    """Así se despliega el endurecimiento: midiendo qué descartaría sobre
    tráfico real antes de descartar. El riesgo no es teórico — un grounding
    demasiado estricto ya dejó al analista contestando plantillas."""
    from app.config import settings
    monkeypatch.setattr(settings, "GROUNDING_MODO_REGISTRO", True)

    veredicto = await validacion.validar(
        hechos={"gasto": 10.0},
        respuesta="El total fue S/ 9999.00.",
        contexto=validacion.Contexto(feature="campaign_chat"),
    )

    assert veredicto.aceptada is True, "en modo de registro no se descarta"
    assert veredicto.motivo == "cifra_sin_respaldo"

    fila = (await db_session.execute(select(AIDiscard))).scalars().one()
    assert fila.aplicado is False, "queda anotado como 'lo habría descartado'"


def test_c18_la_compuerta_de_publicacion_corta_de_verdad():
    """Una compuerta que nunca corta no es una compuerta.

    Se comprueba con las tres degradaciones que debe atajar: seguridad por
    debajo del 100 %, grounding peor que la línea base, y costo por encima de lo
    tolerado. Sin este test, `comparar` podría devolver siempre una lista vacía
    y el conjunto de evaluación pasaría a ser un informe decorativo.
    """
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[1] / "scripts" / "evaluar_cambio_ia.py"
    spec = importlib.util.spec_from_file_location("evaluar_cambio_ia", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    base = {"grounding": 1.0, "seguridad": 1.0, "costo_relativo": 100.0}

    # Sin degradación: publicable.
    assert modulo.comparar(
        {"grounding": 1.0, "seguridad": 1.0, "costo_relativo": 100.0}, base
    ) == []

    # Un solo fallo de seguridad bloquea.
    problemas = modulo.comparar(
        {"grounding": 1.0, "seguridad": 0.95, "costo_relativo": 100.0}, base
    )
    assert any("seguridad" in p for p in problemas)

    # El grounding no puede bajar ni un caso.
    problemas = modulo.comparar(
        {"grounding": 0.99, "seguridad": 1.0, "costo_relativo": 100.0}, base
    )
    assert any("grounding bajó" in p for p in problemas)

    # El costo por encima del margen tolerado.
    problemas = modulo.comparar(
        {"grounding": 1.0, "seguridad": 1.0, "costo_relativo": 130.0}, base
    )
    assert any("costo subió" in p for p in problemas)


def test_c18_existe_una_linea_base_registrada():
    """El criterio pide "una ejecución registrada que compare un cambio
    candidato contra la línea base". Sin línea base versionada no hay contra
    qué comparar, y la primera publicación después de un cambio de prompt sería
    a ciegas."""
    import json as json_mod
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "evaluacion" / "resultados" / "linea-base.json"
    assert base.exists(), "falta evaluacion/resultados/linea-base.json"

    datos = json_mod.loads(base.read_text())
    assert datos["seguridad"] == 1.0
    assert datos["grounding"] == 1.0
    assert datos["casos"] >= 20


@pytest.mark.asyncio
async def test_el_modelo_declarado_no_se_sustituye_en_silencio(db_session, monkeypatch):
    """Un modelo ausente del catálogo del proveedor se usaba reemplazándolo por
    el primero de la lista. La intención era evitar un 404 que no explica nada;
    el efecto era llamar a un modelo que nadie eligió, con otro precio, y el
    único rastro era la factura."""
    from app.config import settings
    from app.services.ai.llm import pick_model

    org = _org(llm_provider="openrouter", ai_model="modelo-que-no-existe")
    assert pick_model(org, "openrouter") == "modelo-que-no-existe"

    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-key")
    falso = _ProveedorControlado(contenido=json.dumps({"text": "ok"}))
    monkeypatch.setattr(httpx, "AsyncClient", falso)

    db_session.add(org)
    await db_session.commit()
    await OpenRouterClient(organization=org).compose_chat_answer(
        org, "x", "SPEND", {"gasto": 1.0},
    )

    assert falso.llamadas[0]["payload"]["model"] == "modelo-que-no-existe"


# --------------------------------------------------------------------------
# La superficie real: la pantalla de Configuración.
# --------------------------------------------------------------------------

async def _agencia(async_client: AsyncClient, slug: str, provider="openai"):
    from app.config import settings
    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@ejemplo.com", "password": "securepassword123",
        "full_name": "Agencia", "organization_slug": slug, "organization_name": slug,
    })
    return {"Authorization": f"Bearer {alta.json()['access_token']}"}


@pytest.mark.asyncio
async def test_c10_cambiar_el_modelo_desde_la_pantalla_queda_en_la_bitacora(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """El criterio 10 no se cumple con una función que sabe registrar: se cumple
    cuando la superficie que la gente usa la llama."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "bitacora-org")

    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"ai_model": "gpt-5.4-mini"},
    )
    assert res.status_code == 200, res.text

    fila = (await db_session.execute(
        select(AIConfigChange).where(AIConfigChange.campo == "ai_model")
    )).scalars().one()
    assert fila.valor_anterior == "gpt-5.6-luna"
    assert fila.valor_nuevo == "gpt-5.4-mini"
    assert fila.actor_email == "bitacora-org@ejemplo.com"


@pytest.mark.asyncio
async def test_c11_no_se_puede_guardar_un_modelo_de_otro_proveedor(
    async_client: AsyncClient, monkeypatch,
):
    """Guardarlo dejaba al cliente con todas sus llamadas fallando y la pantalla
    mostrando el modelo elegido como si nada."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "ajeno-org")

    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"ai_model": "google/gemini-2.5-pro"},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "AI_MODEL_NOT_ELIGIBLE"


@pytest.mark.asyncio
async def test_c12_no_se_puede_guardar_un_modelo_retirado(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "retirado-org")

    await catalogo.retirar(db_session, "openai", "gpt-5.4-mini",
                           motivo="lo retiró el proveedor", definitivo=True)
    await db_session.commit()

    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"ai_model": "gpt-5.4-mini"},
    )

    assert res.status_code == 400
    assert "retiró el proveedor" in res.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_c11_un_modelo_del_catalogo_si_se_guarda(
    async_client: AsyncClient, monkeypatch,
):
    """Contracaso. Sin él, la validación se aprueba rechazando todo y la
    pantalla de Configuración deja de poder cambiar el modelo."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "valido-org")

    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"ai_model": "gpt-5.4-nano"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["ai_model"] == "gpt-5.4-nano"


@pytest.mark.asyncio
async def test_c10_cambiar_proveedor_y_modelo_a_la_vez_se_valida_junto(
    async_client: AsyncClient, monkeypatch,
):
    """Validar el modelo contra el proveedor NUEVO, no contra el guardado.

    Comprobarlo contra el viejo rechazaría todo cambio de proveedor: ningún
    modelo de OpenRouter es elegible mientras la organización siga declarando
    OpenAI.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "mudanza-org")

    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"llm_provider": "openrouter", "ai_model": "google/gemini-2.5-pro"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["llm_provider"] == "openrouter"


@pytest.mark.asyncio
async def test_un_proveedor_inventado_se_rechaza(async_client: AsyncClient, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "inventado-org")
    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"llm_provider": "proveedor-que-no-existe"},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "AI_PROVIDER_INVALID"


@pytest.mark.asyncio
async def test_el_techo_de_ia_se_guarda_como_decimal_exacto(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """Llega como texto y se guarda como `Decimal`. Si pasara por `float`, un
    techo de 0.10 quedaría como 0.1000000000000000055 y la comparación de la
    compuerta decidiría por un residuo binario."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "techo-org")
    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"ai_monthly_budget_usd": "0.10"},
    )
    assert res.status_code == 200, res.text

    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "techo-org")
    )).scalar_one()
    assert org.ai_monthly_budget_usd == Decimal("0.10")


@pytest.mark.asyncio
async def test_un_techo_de_ia_negativo_se_rechaza(async_client: AsyncClient, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "negativo-org")
    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers,
        json={"ai_monthly_budget_usd": "-5"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "AI_BUDGET_INVALID"


@pytest.mark.asyncio
async def test_el_cliente_no_ve_el_techo_de_ia(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """Proveedor, modelo y consumo son palancas de la agencia (dependencia 05).
    El cliente no negocia su presupuesto de IA en la pantalla."""
    from app.config import settings
    from app.models.organization_user import OrganizationUser
    from app.services.experiencia import ACCESO_CLIENTE

    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")
    headers = await _agencia(async_client, "cliente-ve-org")

    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "cliente-ve-org")
    )).scalar_one()
    org.ai_monthly_budget_usd = Decimal("50.00")
    membresia = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org.id)
    )).scalars().first()
    membresia.access_kind = ACCESO_CLIENTE
    await db_session.commit()

    cuerpo = (await async_client.get("/api/v1/organizations/my", headers=headers)).json()

    assert cuerpo.get("ai_monthly_budget_usd") is None
    assert cuerpo.get("llm_provider") is None


# --------------------------------------------------------------------------
# Observabilidad: consumo, margen y descartes.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_el_panel_de_presupuesto_sale_de_la_misma_fuente_que_la_compuerta(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """Si la pantalla lo calculara por su cuenta, podría mostrar margen mientras
    el backend bloquea — la discrepancia más difícil de diagnosticar desde una
    captura de pantalla."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "panel-org")
    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "panel-org")
    )).scalar_one()
    org.ai_monthly_budget_usd = Decimal("10.00")
    await db_session.commit()
    await _gastar(db_session, org.id, "7.25")

    cuerpo = (await async_client.get("/api/v1/ai-usage/budget", headers=headers)).json()

    # Se compara el VALOR, no su forma: la columna tiene cuatro decimales y el
    # consumo seis, así que "10.0000" y "10.00" son el mismo techo. Lo que este
    # test cuida es la exactitud, no el formateo.
    assert Decimal(cuerpo["limite_usd"]) == Decimal("10.00")
    assert Decimal(cuerpo["consumido_usd"]) == Decimal("7.25")
    assert cuerpo["umbral_alcanzado"] == 70
    assert cuerpo["agotado"] is False
    # Los montos viajan como texto: un Decimal serializado a float reintroduce
    # el residuo binario que la spec 02 saca de la decisión.
    assert isinstance(cuerpo["limite_usd"], str)


@pytest.mark.asyncio
async def test_los_descartes_se_pueden_consultar_por_motivo(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """Es lo que convierte "el chat suena a plantilla" en un diagnóstico."""
    from app.config import settings
    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")

    headers = await _agencia(async_client, "descartes-org")
    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "descartes-org")
    )).scalar_one()

    for respuesta in ("Son S/ 9999.00.", "Son S/ 8888.00."):
        await validacion.validar(
            hechos={"gasto": 10.0}, respuesta=respuesta,
            contexto=validacion.Contexto(
                feature="campaign_chat", organization_id=org.id,
            ),
        )

    cuerpo = (await async_client.get("/api/v1/ai-usage/discards", headers=headers)).json()

    assert cuerpo["aplicados"] == 2
    assert cuerpo["solo_registrados"] == 0
    assert cuerpo["por_motivo"][0]["motivo"] == "cifra_sin_respaldo"
    # Sin llamadas registradas, la tasa es None y no 0: "sin datos" no es
    # "perfecto".
    assert cuerpo["tasa"] is None


@pytest.mark.asyncio
async def test_el_cliente_no_ve_el_consumo_ni_los_descartes(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    from app.config import settings
    from app.models.organization_user import OrganizationUser
    from app.services.experiencia import ACCESO_CLIENTE

    monkeypatch.setattr(settings, "DEFAULT_AI_PROVIDER", "openai")
    headers = await _agencia(async_client, "cliente-obs-org")

    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "cliente-obs-org")
    )).scalar_one()
    membresia = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org.id)
    )).scalars().first()
    membresia.access_kind = ACCESO_CLIENTE
    await db_session.commit()

    assert (await async_client.get("/api/v1/ai-usage/budget", headers=headers)).status_code == 403
    assert (await async_client.get("/api/v1/ai-usage/discards", headers=headers)).status_code == 403


# --------------------------------------------------------------------------
# Criterio 6, en la superficie: el chat dice de dónde viene cada respuesta.
# --------------------------------------------------------------------------

def test_c6_la_respuesta_del_chat_declara_su_origen(monkeypatch):
    """Antes, "el asistente no entiende" y "el asistente no está disponible"
    llegaban al cliente como el mismo texto, y ninguna de las dos se podía
    diagnosticar desde una captura de pantalla."""
    from app.config import settings
    from app.services.campaign_chat import cards

    monkeypatch.setattr(settings, "MOCK_SERVICES", False)

    del_modelo = cards.text_message("Gastaste S/ 100.", origen_de_la_respuesta=origen.ORIGEN_MODELO)
    assert del_modelo["origen"] == "modelo"
    # Que el modelo redacte es lo esperado: no se anuncia en cada mensaje.
    assert "origen_label" not in del_modelo

    del_codigo = cards.text_message("Gastaste S/ 100.", origen_de_la_respuesta=origen.ORIGEN_DETERMINISTA)
    assert del_codigo["origen"] == "determinista"
    assert del_codigo["origen_label"]


def test_c6_el_mensaje_del_usuario_no_lleva_origen():
    """El origen califica lo que produce el sistema, no lo que escribió la
    persona. Marcarlo ahí sería ruido y, peor, sugeriría que su propio mensaje
    lo generó algo."""
    from app.services.campaign_chat import cards

    msg = cards.user_text_message("hola")
    assert "origen" not in msg


def test_c6_un_mensaje_sin_origen_declarado_no_inventa_uno():
    """Los cientos de mensajes del flujo de creación no declaran origen. Que el
    campo esté ausente es distinto de afirmar que lo escribió el modelo."""
    from app.services.campaign_chat import cards

    assert "origen" not in cards.text_message("¿Cómo se llama el producto?")


@pytest.mark.asyncio
async def test_c6_con_el_presupuesto_agotado_el_chat_marca_y_explica(
    db_session: AsyncSession, monkeypatch,
):
    """Las dos mitades del criterio 14 y del 6 a la vez: sigue respondiendo, lo
    dice, y la respuesta queda identificada como determinista."""
    from app.services.campaign_chat.engine import _nota_de_ia_no_disponible

    org = _org(ai_monthly_budget_usd=Decimal("1.00"))
    db_session.add(org)
    await db_session.commit()
    await _gastar(db_session, org.id, "1.00")

    with pytest.raises(presupuesto.PresupuestoDeIAAgotado) as e:
        await presupuesto.assert_hay_presupuesto(db_session, org)

    nota = _nota_de_ia_no_disponible(e.value.as_detail())
    # Se nombra lo que SIGUE funcionando, no solo lo que se perdió: la respuesta
    # que acompaña la nota es correcta, con cifras reales.
    assert "sistema" in nota and "datos reales" in nota
