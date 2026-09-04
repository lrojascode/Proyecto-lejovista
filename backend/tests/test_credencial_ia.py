"""Resolver la credencial de IA no puede fallar en silencio.

En producción la key quedó guardada en una organización (`default`) y el chat
corría en otra (`lejovista`). `_resolve_config` exigía key **y** proveedor
juntos, así que la organización sin ellos caía al último caso: OpenRouter con
key vacía. Cada llamada daba 401, cada 401 devolvía `None`, y el chat contestó
con textos de plantilla durante días.

Nada se veía roto. La pantalla de Configuración mostraba la key y el modelo
elegido como si todo estuviera conectado. Eso es lo que estos tests impiden.
"""
import pytest

from app.models.organization import Organization
from app.services.ai import llm
from app.services.ai.llm import OpenRouterClient


@pytest.fixture(autouse=True)
def _sin_credenciales_globales(monkeypatch):
    """Sin esto, la key del entorno tapa exactamente lo que se quiere probar."""
    monkeypatch.setattr(llm.settings, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(llm.settings, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(llm.settings, "OPENROUTER_API_KEY", "", raising=False)
    llm._SIN_CREDENCIAL_AVISADO.clear()
    yield
    llm._SIN_CREDENCIAL_AVISADO.clear()


def _org(slug="test", key=None, provider=None):
    org = Organization(slug=slug, name=slug, status="ACTIVE")
    org.llm_provider = provider
    if key:
        org.set_llm_api_key(key)
    return org


# --- Una key guardada sin proveedor: falla, pero en voz alta ---

def test_una_key_sin_proveedor_no_se_usa_pero_tampoco_se_calla():
    """Antes esta key se usaba, deduciendo el proveedor de su forma. La spec 10
    lo prohíbe: rotar la credencial de un cliente por una de otro proveedor le
    cambiaba proveedor, modelo y precio sin que nadie lo decidiera.

    Lo que NO cambia es la lección del incidente que originó este archivo: la
    credencial no se descarta en silencio. Ahora el fallo dice qué falta y dónde
    cargarlo, que es mejor que resolver a un proveedor que nadie eligió.
    """
    from app.services.ai import proveedor

    org = _org(key="sk-proj-abc123", provider=None)
    cliente = OpenRouterClient(organization=org)

    api_key, _url, _provider, _ = cliente._resolve_config(org)

    assert api_key == "", "sin proveedor declarado no se llama a nadie"
    assert isinstance(cliente.config_error, proveedor.ProveedorNoConfigurado)
    # El mensaje tiene que ser accionable: quién y qué hacer.
    assert "test" in str(cliente.config_error)
    assert "Configuración" in str(cliente.config_error)


@pytest.mark.parametrize("key,esperado", [
    ("sk-proj-abc", "openai"),
    ("sk-or-v1-abc", "openrouter"),
    ("AIzaSyAbc123", "gemini_direct"),
])
def test_la_forma_de_la_key_sigue_sirviendo_para_diagnosticar(key, esperado):
    """`_proveedor_por_forma` ya no ELIGE nada (spec 10). Se conserva como
    diagnóstico: lo usa la migración que fijó el proveedor de facto de cada
    organización, y sirve para avisar "esta clave no parece de este proveedor".
    """
    assert llm._proveedor_por_forma(key) == esperado


def test_el_proveedor_guardado_le_gana_a_la_deduccion():
    org = _org(key="sk-abc", provider="openrouter")
    _, url, provider, _ = OpenRouterClient(organization=org)._resolve_config(org)
    assert provider == "openrouter" and "openrouter.ai" in url


# --- Quedarse sin credencial tiene que gritar ---

def test_sin_credencial_se_loguea_un_error_con_el_slug(caplog):
    """El síntoma es "el analista contesta con plantillas". La causa tiene que
    estar en los logs con el nombre de la organización, o se buscan días en el
    lugar equivocado."""
    org = _org(slug="lejovista", key=None, provider=None)

    with caplog.at_level("ERROR"):
        api_key, _, _, _ = OpenRouterClient(organization=org)._resolve_config(org)

    assert api_key == ""
    assert any("lejovista" in r.getMessage() for r in caplog.records), caplog.text


def test_el_aviso_no_se_repite_en_cada_mensaje(caplog):
    """Tiene que aparecer, pero una vez: si no, un chat activo llena el log."""
    org = _org(slug="ruidosa")

    with caplog.at_level("ERROR"):
        for _ in range(5):
            OpenRouterClient(organization=org)._resolve_config(org)

    avisos = [r for r in caplog.records if "ruidosa" in str(r.msg) + str(r.args)]
    assert len(avisos) == 1, f"se logueó {len(avisos)} veces"


def test_una_org_con_credencial_no_dispara_el_aviso(caplog):
    org = _org(slug="ok", key="sk-abc", provider="openai")
    with caplog.at_level("ERROR"):
        OpenRouterClient(organization=org)._resolve_config(org)
    assert "no tiene credencial" not in caplog.text


# --- La pantalla tiene que decir la verdad ---

@pytest.mark.asyncio
async def test_ai_models_reporta_si_hay_credencial_de_verdad(async_client, db_session):
    """El caso exacto: la organización no tiene key, el selector muestra un
    modelo, y el endpoint tiene que admitir que no hay con qué llamarlo."""
    signup = await async_client.post("/api/v1/auth/signup", json={
        "email": "cred@example.com", "password": "securepassword123",
        "full_name": "Cred", "organization_slug": "sin-key", "organization_name": "Sin Key",
    })
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    res = await async_client.get("/api/v1/organizations/ai-models", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["credential_ok"] is False
    assert body["effective_model"], "tiene que decir qué modelo usaría"
