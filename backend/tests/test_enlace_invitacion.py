"""El enlace de invitación no puede depender del subdominio del cliente.

`accept_invitation` busca la invitación SOLO por token — la organización sale
de la propia invitación. Apuntar el enlace al subdominio del cliente obligaba a
tener DNS y certificado para ese cliente ANTES de su primer login, que es justo
cuando todavía no hay nada configurado. Y era `http://` fijo: en producción, un
enlace sin TLS por el que viaja la contraseña recién elegida.
"""
import pytest

from app.config import settings
from app.utils.urls import base_url, invitation_url


@pytest.fixture
def dominio(monkeypatch):
    def _set(valor):
        monkeypatch.setattr(settings, "BASE_DOMAIN", valor)
    return _set


def test_en_produccion_usa_https_y_el_dominio_base(dominio):
    dominio("lejovista.agenciakibo.com")
    assert invitation_url("tok123") == "https://lejovista.agenciakibo.com/invitations/tok123"


def test_no_mete_el_subdominio_del_cliente(dominio):
    """Si lo metiera, cada alta necesitaría un registro DNS y un certificado
    antes de que el cliente pudiera abrir el enlace."""
    dominio("lejovista.agenciakibo.com")
    url = invitation_url("tok123")
    assert url.count(".") == 2          # lejovista.agenciakibo.com y nada más
    assert "//lejovista.agenciakibo.com/" in url


def test_en_local_sigue_siendo_http(dominio):
    dominio("localhost:3000")
    assert invitation_url("tok123") == "http://localhost:3000/invitations/tok123"
    assert base_url() == "http://localhost:3000"


def test_nunca_manda_una_contrasena_por_http_en_produccion(dominio):
    for host in ("lejovista.agenciakibo.com", "app.ejemplo.pe", "midominio.com"):
        dominio(host)
        assert invitation_url("t").startswith("https://"), host
