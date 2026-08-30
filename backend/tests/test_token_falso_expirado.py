"""El token eterno de ITS que aparecía como expirado.

El System User de ITS vive en el Business Manager del cliente y su token no
caduca. Aun así el panel mostraba "Token de Meta Expirado" y volver a pegar EL
MISMO token lo arreglaba — o sea que el token nunca estuvo mal. Lo que estaba
mal era cómo se decidía que había expirado, por tres caminos distintos:

1. `debug_token` con la app equivocada devuelve un error DE LA CONSULTA, y eso
   se leía como "token inválido" → la revisión de las 08:00 marcaba EXPIRED.
2. Cualquier `type: OAuthException` se clasificaba como código 190 — incluidos
   los rate limits, que es lo que Meta le manda a esa cuenta todos los días.
3. Una vez EXPIRED, nada la devolvía a ACTIVE salvo reconectar a mano.

Y de fondo: a un token permanente se le estampaban 60 días de caducidad
inventados que nadie borraba nunca.
"""
import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_credential import MetaCredential
from app.models.organization import Organization
from app.services.meta_ads.exceptions import (
    MetaApiError,
    PermanentMetaError,
    TransientMetaError,
    classify_meta_error,
    extract_meta_error_diagnostic,
)
from app.services.meta_ads.token_inspection import (
    ESTADO_INDETERMINADO,
    ESTADO_INVALIDO,
    ESTADO_VALIDO,
    aplicar_inspeccion,
)

# Un rate limit de Meta, tal cual lo devuelve la Graph API. Comparte el
# `type: OAuthException` con la expiración de un token: esa es toda la trampa.
RATE_LIMIT = {
    "error": {
        "message": "(#80004) There have been too many calls from this ad-account.",
        "type": "OAuthException",
        "code": 80004,
        "error_subcode": 2446079,
        "fbtrace_id": "AbC190dEfGh",
    }
}

TOKEN_MUERTO = {
    "error": {
        "message": "Error validating access token: Session has expired.",
        "type": "OAuthException",
        "code": 190,
        "error_subcode": 463,
        "fbtrace_id": "XyZ123",
    }
}

SIN_PERMISO = {
    "error": {
        "message": "(#200) Requires ads_management permission to manage the object",
        "type": "OAuthException",
        "code": 200,
        "fbtrace_id": "Qq7",
    }
}


# --- 1. El clasificador deja de adivinar ---------------------------------

def test_un_rate_limit_no_es_un_token_expirado():
    code, detalle = extract_meta_error_diagnostic(RATE_LIMIT)
    assert code == 80004
    assert "rate limit" in detalle
    assert "Reconecta" not in detalle


def test_un_rate_limit_envuelto_en_excepcion_tampoco():
    """Como llega de verdad: el sync levanta la excepción con el payload."""
    exc = MetaApiError("Error al obtener campañas", payload=RATE_LIMIT)
    assert extract_meta_error_diagnostic(exc)[0] == 80004
    assert isinstance(classify_meta_error(exc), TransientMetaError)


def test_un_fbtrace_id_con_190_adentro_no_expira_nada():
    """`"190" in mensaje` alcanzaba para marcar la cuenta. El `fbtrace_id` de
    RATE_LIMIT lo tiene."""
    assert "190" in str(RATE_LIMIT)
    assert extract_meta_error_diagnostic(RATE_LIMIT)[0] != 190


def test_un_error_de_permisos_no_es_un_token_expirado():
    code, detalle = extract_meta_error_diagnostic(SIN_PERMISO)
    assert code == 200
    assert "permiso" in detalle


def test_un_token_muerto_de_verdad_si_se_detecta():
    code, detalle = extract_meta_error_diagnostic(TOKEN_MUERTO)
    assert code == 190
    assert "expirado" in detalle
    assert isinstance(classify_meta_error(MetaApiError("x", payload=TOKEN_MUERTO)), PermanentMetaError)


def test_un_texto_suelto_sobre_el_token_sigue_valiendo():
    """Los errores que llegan sin código estructurado (excepciones nuestras)."""
    code, _ = extract_meta_error_diagnostic(Exception("Error validating access token: bla"))
    assert code == 190


# --- 2. Una consulta que no se pudo hacer no concluye nada ---------------

class _Res:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _ClientSecuencia:
    """Devuelve una respuesta por llamada, en orden, y anota las URLs."""

    def __init__(self, respuestas, urls):
        self._respuestas = respuestas
        self._urls = urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self._urls.append(url)
        return self._respuestas.pop(0)


def _fijar_meta_real(mocker):
    from app.config import settings as app_settings

    mocker.patch.object(app_settings, "MOCK_SERVICES", False)
    mocker.patch.object(app_settings, "META_APP_ID", "app_de_la_agencia")
    mocker.patch.object(app_settings, "META_APP_SECRET", "secreto_agencia")


@pytest.mark.asyncio
async def test_app_equivocada_mas_cuenta_que_responde_es_token_sano(mocker):
    """El caso de ITS: `debug_token` no puede responder porque preguntamos con la
    app de la agencia, pero el token lee la cuenta sin problema."""
    from app.services.meta_ads import token_inspection

    _fijar_meta_real(mocker)
    urls = []
    respuestas = [
        # debug_token: error de PRIMER nivel = de la consulta, no del token.
        _Res({"error": {"message": "The token does not belong to app", "code": 100}}, 400),
        # el sondeo contra la cuenta publicitaria: el token funciona.
        _Res({"id": "act_its"}),
    ]
    mocker.patch.object(
        token_inspection.httpx, "AsyncClient",
        lambda **kw: _ClientSecuencia(respuestas, urls),
    )

    res = await token_inspection.inspect_token_expiration(
        "token-de-its", external_account_id="act_its"
    )
    assert res["estado"] == ESTADO_VALIDO
    assert res["expires_at"] is None
    assert "act_its" in urls[-1]


@pytest.mark.asyncio
async def test_si_el_sondeo_devuelve_190_si_esta_expirado(mocker):
    from app.services.meta_ads import token_inspection

    _fijar_meta_real(mocker)
    respuestas = [
        _Res({"error": {"message": "does not belong to app", "code": 100}}, 400),
        _Res(TOKEN_MUERTO, 400),
    ]
    mocker.patch.object(
        token_inspection.httpx, "AsyncClient",
        lambda **kw: _ClientSecuencia(respuestas, []),
    )

    res = await token_inspection.inspect_token_expiration("muerto", external_account_id="act_its")
    assert res["estado"] == ESTADO_INVALIDO


@pytest.mark.asyncio
async def test_un_rate_limit_en_el_sondeo_no_concluye_nada(mocker):
    from app.services.meta_ads import token_inspection

    _fijar_meta_real(mocker)
    respuestas = [
        _Res({"error": {"message": "does not belong to app", "code": 100}}, 400),
        _Res(RATE_LIMIT, 400),
    ]
    mocker.patch.object(
        token_inspection.httpx, "AsyncClient",
        lambda **kw: _ClientSecuencia(respuestas, []),
    )

    res = await token_inspection.inspect_token_expiration("token", external_account_id="act_its")
    assert res["estado"] == ESTADO_INDETERMINADO


@pytest.mark.asyncio
async def test_meta_diciendo_que_el_token_murio_si_es_invalido(mocker):
    """`is_valid: false` DENTRO de `data` sí es un veredicto sobre el token."""
    from app.services.meta_ads import token_inspection

    _fijar_meta_real(mocker)
    respuestas = [_Res({"data": {"is_valid": False, "error": {"message": "expired"}}})]
    mocker.patch.object(
        token_inspection.httpx, "AsyncClient",
        lambda **kw: _ClientSecuencia(respuestas, []),
    )

    res = await token_inspection.inspect_token_expiration("token", external_account_id="act_x")
    assert res["estado"] == ESTADO_INVALIDO


# --- 3. Volcar la inspección sobre la cuenta -----------------------------

def _cuenta(status="ACTIVE"):
    acc = AdAccount(
        organization_id="o", platform="META",
        external_account_id="act_its", name="ITS", status=status,
    )
    acc.set_access_token("token-de-its")
    return acc, acc.meta_credential


def test_un_indeterminado_no_toca_el_estado_de_la_cuenta():
    acc, cred = _cuenta(status="ACTIVE")
    estado = aplicar_inspeccion(acc, cred, {"estado": ESTADO_INDETERMINADO, "error": "sin red"})
    assert estado == ESTADO_INDETERMINADO
    assert acc.status == "ACTIVE"
    assert acc.last_sync_error_code is None


def test_un_token_valido_devuelve_la_cuenta_a_activa_y_borra_la_fecha_falsa():
    acc, cred = _cuenta(status="EXPIRED")
    acc.last_sync_error_code = 190
    acc.last_sync_error_detail = "Token de acceso de Meta expirado o revocado."
    cred.status = "EXPIRED"
    # La caducidad inventada de 60 días que dejaba `set_access_token`.
    assert cred.token_expires_at is not None

    aplicar_inspeccion(acc, cred, {"estado": ESTADO_VALIDO, "expires_at": None})

    assert acc.status == "ACTIVE"
    assert cred.status == "ACTIVE"
    assert acc.last_sync_error_code is None
    assert cred.token_expires_at is None


def test_un_diagnostico_que_no_es_de_token_sobrevive():
    """Un rate limit anotado en la cuenta no se borra al confirmar el token."""
    acc, cred = _cuenta()
    acc.last_sync_error_code = 80004
    acc.last_sync_error_detail = "Meta está limitando las peticiones."

    aplicar_inspeccion(acc, cred, {"estado": ESTADO_VALIDO, "expires_at": None})

    assert acc.last_sync_error_code == 80004


@pytest.mark.asyncio
async def test_la_revision_diaria_no_marca_expired_lo_que_no_pudo_verificar(
    db_session: AsyncSession, mocker
):
    from app.services.meta_ads import token_inspection

    org = Organization(slug="indet", name="ITS", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_its", name="ITS", status="ACTIVE",
    )
    acc.set_access_token("token-de-its")
    db_session.add(acc)
    await db_session.commit()

    async def _indeterminado(*a, **kw):
        return {"estado": ESTADO_INDETERMINADO, "is_valid": False,
                "expires_at": None, "scopes": [], "error": "no se pudo preguntar"}

    mocker.patch.object(token_inspection, "inspect_token_expiration", _indeterminado)
    avisos = mocker.patch.object(token_inspection, "_notify_admins_by_email")

    resultados = await token_inspection.check_expiring_tokens(db_session)

    await db_session.refresh(acc)
    assert acc.status == "ACTIVE"
    assert acc.last_sync_error_code is None
    assert [r["status"] for r in resultados] == ["UNDETERMINED"]
    avisos.assert_not_called()


# --- 4. Un sync exitoso rescata una cuenta mal marcada -------------------

@pytest.mark.asyncio
async def test_un_sync_exitoso_devuelve_la_cuenta_a_activa(db_session: AsyncSession):
    from app.services.meta_ads.sync import MetaAdsSyncService

    org = Organization(slug="rescate", name="Rescate", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Rescate", status="EXPIRED",
    )
    acc.set_access_token("token-sano")
    acc.meta_credential.status = "EXPIRED"
    acc.last_sync_error_code = 190
    db_session.add(acc)
    await db_session.commit()

    job = await MetaAdsSyncService(db_session).run_sync(acc, "INCREMENTAL")
    assert job.status == "COMPLETED"

    await db_session.refresh(acc)
    assert acc.status == "ACTIVE"
    assert acc.last_sync_error_code is None


# --- 5. Nunca más una caducidad inventada --------------------------------

def test_un_token_permanente_se_guarda_sin_caducidad():
    cred = MetaCredential(organization_id="o")
    cred.set_access_token("token-de-system-user", never_expires=True)
    assert cred.token_expires_at is None
    assert cred.is_expired is False


def test_sin_dato_de_meta_se_sigue_asumiendo_el_ttl_de_long_lived():
    """El default no cambia: 60 días para el token que sí caduca y del que no
    sabemos la fecha."""
    cred = MetaCredential(organization_id="o")
    cred.set_access_token("token-cualquiera")
    assert cred.token_expires_at is not None
    assert (cred.token_expires_at - datetime.datetime.utcnow()).days >= 59


@pytest.mark.asyncio
async def test_el_canje_sin_expires_in_no_inventa_60_dias(mocker):
    """Meta omite `expires_in` justamente cuando el token ya no caduca."""
    from app.services.meta_ads import token_exchange

    _fijar_meta_real(mocker)
    mocker.patch.object(
        token_exchange.httpx, "AsyncClient",
        lambda **kw: _ClientSecuencia([_Res({"access_token": "largo"})], []),
    )

    token, expires_at = await token_exchange.exchange_for_long_lived_token("corto")
    assert token == "largo"
    assert expires_at is None
