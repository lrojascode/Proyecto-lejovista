"""App de Meta propia por cuenta (mejoras-futuras §1).

`META_APP_ID`/`META_APP_SECRET` son globales —la app de la agencia— y se usan
para dos cosas: canjear el token por uno long-lived y preguntar por su caducidad
con `debug_token`. Un token emitido por OTRA app no se puede depurar con esas
credenciales: Meta devuelve error, `inspect_token_expiration` lo lee como
`is_valid: False`, y la revisión diaria marca la cuenta EXPIRED y suspende el
sync. Sobre un token perfectamente sano.

Aparece en cuanto un cliente usa su propia app — el caso de ITS, cuyo System
User vive en su Business Manager y solo puede emitir tokens con la app de ese
negocio.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meta_credential import MetaCredential


def _credencial(app_id=None, app_secret=None) -> MetaCredential:
    cred = MetaCredential(organization_id="o")
    cred.set_access_token("un-token")
    cred.set_app_credentials(app_id, app_secret)
    return cred


def test_sin_app_propia_la_credencial_no_declara_ninguna():
    assert _credencial().app_credentials == (None, None)


def test_el_secret_de_la_app_se_guarda_cifrado():
    cred = _credencial("123", "el-secreto")
    assert cred.app_secret_encrypted
    assert "el-secreto" not in cred.app_secret_encrypted
    assert cred.get_app_secret() == "el-secreto"
    assert cred.app_credentials == ("123", "el-secreto")


def test_un_app_id_sin_secret_no_sirve_como_par():
    """Usar el app_id propio con el secret global le pega a Meta con un par que
    no existe. O están los dos o no está ninguno."""
    cred = _credencial("123", None)
    assert cred.app_id == "123"
    assert cred.app_credentials == (None, None)


def test_vaciar_el_app_id_vuelve_a_la_app_de_la_agencia():
    cred = _credencial("123", "el-secreto")
    cred.set_app_credentials(None, None)
    assert cred.app_credentials == (None, None)
    assert cred.app_secret_encrypted is None


@pytest.mark.asyncio
async def test_debug_token_usa_la_app_que_emitio_el_token(mocker):
    """El bug que motivó todo: preguntar por un token con las credenciales de
    otra app devuelve error, y ese error se lee como "token inválido"."""
    from app.config import settings as app_settings
    from app.services.meta_ads import token_inspection

    mocker.patch.object(app_settings, "MOCK_SERVICES", False)
    mocker.patch.object(app_settings, "META_APP_ID", "app_de_la_agencia")
    mocker.patch.object(app_settings, "META_APP_SECRET", "secreto_agencia")

    capturado = {}

    class _Res:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"is_valid": True, "expires_at": 0, "scopes": ["ads_read"]}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            capturado.update(params or {})
            return _Res()

    mocker.patch.object(token_inspection.httpx, "AsyncClient", lambda **kw: _Client())

    res = await token_inspection.inspect_token_expiration(
        "token-de-its", "app_de_its", "secreto_its"
    )
    assert capturado["access_token"] == "app_de_its|secreto_its"
    assert res["is_valid"] is True
    # `expires_at: 0` en Meta significa que no expira nunca (System User).
    assert res["expires_at"] is None


@pytest.mark.asyncio
async def test_sin_app_propia_se_usa_la_global(mocker):
    from app.config import settings as app_settings
    from app.services.meta_ads import token_inspection

    mocker.patch.object(app_settings, "MOCK_SERVICES", False)
    mocker.patch.object(app_settings, "META_APP_ID", "app_de_la_agencia")
    mocker.patch.object(app_settings, "META_APP_SECRET", "secreto_agencia")

    capturado = {}

    class _Res:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"is_valid": True, "expires_at": 0, "scopes": []}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            capturado.update(params or {})
            return _Res()

    mocker.patch.object(token_inspection.httpx, "AsyncClient", lambda **kw: _Client())

    await token_inspection.inspect_token_expiration("token-normal")
    assert capturado["access_token"] == "app_de_la_agencia|secreto_agencia"


@pytest.mark.asyncio
async def test_el_canje_usa_la_app_propia(mocker):
    from app.config import settings as app_settings
    from app.services.meta_ads import token_exchange

    mocker.patch.object(app_settings, "MOCK_SERVICES", False)
    mocker.patch.object(app_settings, "META_APP_ID", "app_de_la_agencia")
    mocker.patch.object(app_settings, "META_APP_SECRET", "secreto_agencia")

    capturado = {}

    class _Res:
        @staticmethod
        def json():
            return {"access_token": "largo", "expires_in": 5184000}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            capturado.update(params or {})
            return _Res()

    mocker.patch.object(token_exchange.httpx, "AsyncClient", lambda **kw: _Client())

    token, _ = await token_exchange.exchange_for_long_lived_token(
        "corto", "app_de_its", "secreto_its"
    )
    assert token == "largo"
    assert capturado["client_id"] == "app_de_its"
    assert capturado["client_secret"] == "secreto_its"


@pytest.mark.asyncio
async def test_la_revision_diaria_no_marca_expired_una_app_propia(
    db_session: AsyncSession, mocker
):
    """El final del camino: sin pasarle la app de la credencial, `debug_token`
    fallaba y `check_expiring_tokens` marcaba EXPIRED, suspendía el sync y
    mandaba mail a los admins."""
    from app.models.ad_account import AdAccount
    from app.models.organization import Organization
    from app.services.meta_ads import token_inspection

    org = Organization(slug="app-propia", name="ITS", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_its", name="ITS", status="ACTIVE",
    )
    acc.set_access_token("token-de-its")
    acc.meta_credential.set_app_credentials("app_de_its", "secreto_its")
    db_session.add(acc)
    await db_session.commit()

    vistos = []

    async def _espia(user_token, app_id=None, app_secret=None):
        vistos.append((app_id, app_secret))
        return {"is_valid": True, "expires_at": None, "scopes": [], "error": None}

    mocker.patch.object(token_inspection, "inspect_token_expiration", _espia)

    await token_inspection.check_expiring_tokens(db_session)

    assert vistos == [("app_de_its", "secreto_its")]
    await db_session.refresh(acc)
    assert acc.status == "ACTIVE"
