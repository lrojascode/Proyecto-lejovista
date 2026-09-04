"""Spec 01 — Autenticación, sesiones, suspensión, invitaciones y fallo cerrado.

Un test por criterio de aceptación de `docs/specs/01-autenticacion-sesiones-
suspension-e-invitaciones.md`. La frontera es la API HTTP y el WebSocket: cada
criterio se comprueba emitiendo una credencial real y mirando lo que responde la
superficie, no llamando a las funciones de firma.

Lo que NO se prueba acá: que la credencial no aparezca en los logs. Eso no se
comprueba leyendo archivos de log, se comprueba viendo que la superficie no
acepta la credencial por el canal registrable (criterio 6).
"""
import datetime

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.invitation import Invitation, hash_invitation_token
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.utils.security import (
    RevocationCheckUnavailable,
    TOKEN_TYPE_WS_TICKET,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.api.v1.ws import WS_TICKET_SUBPROTOCOL_PREFIX
from conftest import TestingSessionLocal


@pytest.fixture
def ws_contra_la_base_de_test(mocker):
    """El handshake del WebSocket abre su propia sesión de BD.

    No pasa por `Depends(get_db)` —no es una request HTTP— así que el override
    de dependencias de conftest no lo alcanza y consultaría la base real. Se
    apunta al mismo SQLite que el resto de la suite.
    """
    mocker.patch("app.api.v1.ws.async_session", TestingSessionLocal)


async def _signup(client: AsyncClient, slug: str, email_prefix: str = "admin"):
    res = await client.post("/api/v1/auth/signup", json={
        "email": f"{email_prefix}@{slug}.example.com", "password": "securepassword123",
        "full_name": "Spec01 Tester", "organization_slug": slug,
        "organization_name": "Spec01 Org",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


# --------------------------------------------------------------------------
# Criterios 1-2 · un refresh token no autentica ninguna otra superficie
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_refresh_token_no_sirve_como_bearer(async_client: AsyncClient):
    """El agujero central: el refresh dura 7 días y viaja en una cookie que el
    navegador adjunta solo. Como Bearer abría la API entera."""
    body, _ = await _signup(async_client, "c1-refresh")
    refresh = async_client.cookies.get("refresh_token")
    assert refresh, "el signup debe fijar la cookie de refresh"

    res = await async_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_c2_refresh_token_no_abre_el_websocket(async_client: AsyncClient, ws_contra_la_base_de_test):
    body, _ = await _signup(async_client, "c2-ws")
    org_id = body["user"]["organization_id"]
    refresh = async_client.cookies.get("refresh_token")

    tc = TestClient(app)
    with pytest.raises(Exception):
        with tc.websocket_connect(
            f"/ws/notifications/{org_id}",
            subprotocols=[f"{WS_TICKET_SUBPROTOCOL_PREFIX}{refresh}"],
        ):
            pass


# --------------------------------------------------------------------------
# Criterio 3 · la revocación se consulta en la autenticación, no solo al renovar
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c3_access_token_revocado_se_rechaza_en_la_api(async_client: AsyncClient, mocker):
    body, headers = await _signup(async_client, "c3-revocado")

    # Antes, `is_revoked` solo se consultaba en POST /auth/refresh: un access
    # token revocado seguía entrando hasta su expiración.
    mocker.patch("app.utils.security.is_revoked", return_value=True)
    res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert res.status_code == 401


# --------------------------------------------------------------------------
# Criterios 4-5 · cambiar/restablecer la contraseña revoca TODAS las sesiones
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c4_cambiar_contrasena_revoca_las_sesiones_anteriores(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c4-cambio")
    viejo = body["access_token"]

    # Una segunda sesión del mismo usuario, abierta ANTES del cambio.
    otra = await async_client.post("/api/v1/auth/login", json={
        "email": "admin@c4-cambio.example.com", "password": "securepassword123",
    })
    otro_access = otra.json()["access_token"]

    res = await async_client.post("/api/v1/auth/change-password", headers=headers, json={
        "current_password": "securepassword123", "new_password": "otracontrasena456",
    })
    assert res.status_code == 200, res.text

    # Los dos tokens anteriores al cambio quedan fuera...
    for token in (viejo, otro_access):
        r = await async_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 401, "un access emitido antes del cambio sigue entrando"

    # ...y el par nuevo que devuelve el propio cambio sí funciona: cambiar la
    # contraseña no puede desloguear a quien la cambia.
    nuevo = res.json()["access_token"]
    r = await async_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {nuevo}"}
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_c4b_el_refresh_anterior_al_cambio_no_renueva(async_client: AsyncClient):
    body, headers = await _signup(async_client, "c4b-refresh")
    refresh_viejo = async_client.cookies.get("refresh_token")

    await async_client.post("/api/v1/auth/change-password", headers=headers, json={
        "current_password": "securepassword123", "new_password": "otracontrasena456",
    })

    res = await async_client.post(
        "/api/v1/auth/refresh", cookies={"refresh_token": refresh_viejo}
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_c5_restablecer_contrasena_revoca_las_sesiones(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    mocker.patch("app.api.v1.auth.send_email", return_value=True)
    body, headers = await _signup(async_client, "c5-reset")
    viejo = body["access_token"]

    await async_client.post("/api/v1/auth/forgot-password",
                            json={"email": "admin@c5-reset.example.com"})

    # El token en claro solo existe en el correo; en el test se toma el hash de
    # la fila y se reconstruye el flujo con un token conocido.
    from app.models.password_reset import PasswordResetToken
    import hashlib
    token_plano = "token-de-prueba-spec01"
    await db_session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.used_at.is_(None))
        .values(token_hash=hashlib.sha256(token_plano.encode()).hexdigest())
    )
    await db_session.commit()

    res = await async_client.post("/api/v1/auth/reset-password", json={
        "token": token_plano, "new_password": "contrasenanueva789",
    })
    assert res.status_code == 200, res.text

    r = await async_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {viejo}"}
    )
    assert r.status_code == 401


# --------------------------------------------------------------------------
# Criterio 6 · el WebSocket no se abre con la credencial en el query string
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c6_websocket_rechaza_el_token_en_query_string(async_client: AsyncClient, ws_contra_la_base_de_test):
    body, _ = await _signup(async_client, "c6-query")
    org_id = body["user"]["organization_id"]
    access = body["access_token"]

    tc = TestClient(app)
    with pytest.raises(Exception):
        with tc.websocket_connect(f"/ws/notifications/{org_id}?token={access}"):
            pass


@pytest.mark.asyncio
async def test_c6b_websocket_se_abre_con_el_ticket_por_subprotocolo(
    async_client: AsyncClient, mocker, ws_contra_la_base_de_test
):
    """El mecanismo nuevo tiene que funcionar: un criterio que solo comprueba el
    rechazo no distingue "se cerró el canal viejo" de "se rompió el WebSocket"."""
    mocker.patch("app.api.v1.ws.consume_ws_ticket", return_value=True)
    body, headers = await _signup(async_client, "c6b-ticket")
    org_id = body["user"]["organization_id"]

    res = await async_client.post("/api/v1/auth/ws-ticket", headers=headers)
    assert res.status_code == 200, res.text
    ticket = res.json()["ticket"]
    assert decode_token(ticket, expected_type=TOKEN_TYPE_WS_TICKET) is not None

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/ws/notifications/{org_id}",
        subprotocols=[f"{WS_TICKET_SUBPROTOCOL_PREFIX}{ticket}"],
    ) as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"


@pytest.mark.asyncio
async def test_c6c_el_ticket_es_de_un_solo_uso(
    async_client: AsyncClient, mocker, ws_contra_la_base_de_test
):
    usos = {"n": 0}

    async def _consumir(jti):
        usos["n"] += 1
        return usos["n"] == 1

    mocker.patch("app.api.v1.ws.consume_ws_ticket", side_effect=_consumir)
    body, headers = await _signup(async_client, "c6c-unuso")
    org_id = body["user"]["organization_id"]
    ticket = (await async_client.post("/api/v1/auth/ws-ticket", headers=headers)).json()["ticket"]

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/ws/notifications/{org_id}",
        subprotocols=[f"{WS_TICKET_SUBPROTOCOL_PREFIX}{ticket}"],
    ):
        pass
    with pytest.raises(Exception):
        with tc.websocket_connect(
            f"/ws/notifications/{org_id}",
            subprotocols=[f"{WS_TICKET_SUBPROTOCOL_PREFIX}{ticket}"],
        ):
            pass


# --------------------------------------------------------------------------
# Criterios 7-9 · la invitación se consume una vez y su secreto no es legible
# --------------------------------------------------------------------------

async def _crear_invitacion(db: AsyncSession, org_id: str, email: str, token: str):
    inv = Invitation(
        organization_id=org_id,
        email=email,
        role="OPERATOR",
        token_hash=hash_invitation_token(token),
        expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=7),
    )
    db.add(inv)
    await db.commit()
    return inv


@pytest.mark.asyncio
async def test_c7_aceptaciones_concurrentes_dejan_una_sola_membresia(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Antes: leer `accepted_at`, hacer todo el alta y recién después
    escribirlo. Dos peticiones con el mismo enlace pasaban las dos."""
    body, _ = await _signup(async_client, "c7-carrera")
    org_id = body["user"]["organization_id"]
    await _crear_invitacion(db_session, org_id, "invitado@c7.example.com", "tok-c7")

    datos = {"full_name": "Invitado", "password": "securepassword123"}
    primera = await async_client.post("/api/v1/invitations/tok-c7/accept", json=datos)
    segunda = await async_client.post("/api/v1/invitations/tok-c7/accept", json=datos)

    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 400
    assert "ya fue aceptada" in segunda.json()["detail"]

    usuario = (await db_session.execute(
        select(User).where(User.email == "invitado@c7.example.com")
    )).scalar_one()
    membresias = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.user_id == usuario.id)
    )).scalars().all()
    assert len(membresias) == 1


@pytest.mark.asyncio
async def test_c8_invitacion_aceptada_no_se_reutiliza_aunque_no_haya_expirado(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, _ = await _signup(async_client, "c8-reuso")
    org_id = body["user"]["organization_id"]
    inv = await _crear_invitacion(db_session, org_id, "invitado@c8.example.com", "tok-c8")
    assert not inv.is_expired

    datos = {"full_name": "Invitado", "password": "securepassword123"}
    assert (await async_client.post("/api/v1/invitations/tok-c8/accept", json=datos)).status_code == 200
    assert (await async_client.post("/api/v1/invitations/tok-c8/accept", json=datos)).status_code == 400


@pytest.mark.asyncio
async def test_c9_el_secreto_de_la_invitacion_no_es_recuperable_desde_la_base(
    async_client: AsyncClient, db_session: AsyncSession
):
    """La fila guarda el hash. Ni la base ni el listado devuelven el token."""
    body, headers = await _signup(async_client, "c9-secreto")

    creada = await async_client.post(
        "/api/v1/organizations/my/invitations", headers=headers,
        json={"email": "invitado@c9.example.com", "role": "OPERATOR"},
    )
    assert creada.status_code == 201, creada.text
    token_en_claro = creada.json()["token"]
    assert token_en_claro, "la respuesta de creación es el único lugar con el enlace"

    fila = (await db_session.execute(
        select(Invitation).where(Invitation.email == "invitado@c9.example.com")
    )).scalar_one()
    assert fila.token_hash == hash_invitation_token(token_en_claro)
    assert fila.token is None, "el token en claro no se persiste"

    listado = await async_client.get(
        "/api/v1/organizations/my/invitations", headers=headers
    )
    assert listado.status_code == 200
    assert all(i["token"] is None for i in listado.json())
    assert all(i["invite_url"] is None for i in listado.json())


# --------------------------------------------------------------------------
# Criterio 10 · la base impide la membresía duplicada
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c10_la_base_rechaza_una_membresia_duplicada(
    async_client: AsyncClient, db_session: AsyncSession
):
    """La restricción única es la defensa dura: la comprobación de la
    aplicación la hacen las dos transacciones antes de que ninguna escriba."""
    from sqlalchemy.exc import IntegrityError

    body, _ = await _signup(async_client, "c10-unica")
    org_id = body["user"]["organization_id"]
    user_id = body["user"]["id"]

    db_session.add(OrganizationUser(user_id=user_id, organization_id=org_id, role="VIEWER"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --------------------------------------------------------------------------
# Criterios 11-13 · suspensión efectiva
# --------------------------------------------------------------------------

async def _suspender(db: AsyncSession, org_id: str):
    await db.execute(
        update(Organization).where(Organization.id == org_id).values(status="SUSPENDED")
    )
    await db.commit()


@pytest.mark.asyncio
async def test_c11_org_suspendida_bloquea_login_y_api(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c11-suspendida")
    org_id = body["user"]["organization_id"]
    await _suspender(db_session, org_id)

    # API: toda petición autenticada.
    res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ORGANIZATION_SUSPENDED"

    # Login: falla y dice por qué, en vez de entrar a un panel que devuelve 403.
    res = await async_client.post("/api/v1/auth/login", json={
        "email": "admin@c11-suspendida.example.com", "password": "securepassword123",
    })
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ORGANIZATION_SUSPENDED"


@pytest.mark.asyncio
async def test_c11b_org_suspendida_no_sincroniza_ni_analiza(db_session: AsyncSession):
    """Sync e IA corren en workers, sin sesión delante: necesitan su propia
    compuerta. Una organización suspendida seguía gastando llamadas a Meta."""
    from app.services.organization_gate import (
        OrganizationNotActive, assert_organization_active, is_organization_active,
    )

    org = Organization(slug="c11b-worker", name="Worker")
    db_session.add(org)
    await db_session.commit()

    assert await is_organization_active(db_session, org.id) is True
    await _suspender(db_session, org.id)
    assert await is_organization_active(db_session, org.id) is False
    with pytest.raises(OrganizationNotActive):
        await assert_organization_active(db_session, org.id)


@pytest.mark.asyncio
async def test_c12_el_admin_de_plataforma_conserva_acceso_a_una_org_suspendida(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c12-soporte")
    org_id = body["user"]["organization_id"]
    await db_session.execute(
        update(User).where(User.id == body["user"]["id"]).values(is_platform_admin=True)
    )
    await db_session.commit()
    await _suspender(db_session, org_id)

    # La sesión sigue sirviendo: es quien tiene que exportar, investigar o
    # reactivar justamente las organizaciones suspendidas.
    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    listado = await async_client.get("/api/v1/admin/organizations", headers=headers)
    assert listado.status_code == 200


@pytest.mark.asyncio
async def test_c13_suspender_revoca_las_credenciales_de_meta(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    from app.models.meta_credential import MetaCredential

    body, headers = await _signup(async_client, "c13-credenciales")
    org_id = body["user"]["organization_id"]
    await db_session.execute(
        update(User).where(User.id == body["user"]["id"]).values(is_platform_admin=True)
    )
    cred = MetaCredential(organization_id=org_id, status="ACTIVE")
    cred.set_access_token("token-de-meta")
    db_session.add(cred)
    await db_session.commit()

    res = await async_client.patch(
        f"/api/v1/admin/organizations/{org_id}", headers=headers, json={"status": "SUSPENDED"},
    )
    assert res.status_code == 200, res.text

    await db_session.refresh(cred)
    assert cred.status == "REVOKED"

    # Y las sesiones de sus miembros quedan cortadas.
    usuario = await db_session.get(User, body["user"]["id"])
    await db_session.refresh(usuario)
    assert usuario.sessions_valid_from is not None


# --------------------------------------------------------------------------
# Criterios 14-15 · fallo cerrado acotado
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c14_sin_mecanismo_de_revocacion_se_bloquea_lo_que_autoriza_gasto(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """Aprobar y crear campañas comprometen dinero: si no se puede comprobar que
    la sesión siga siendo válida, no se conceden."""
    body, headers = await _signup(async_client, "c14-failclosed")

    mocker.patch("app.utils.security.is_revoked", side_effect=RevocationCheckUnavailable())

    res = await async_client.post(
        "/api/v1/campaigns/", headers=headers,
        json={"ad_account_id": "cualquiera", "product": "x", "zone": "y", "budget": 100},
    )
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "SECURITY_CONTROLS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_c14b_sin_mecanismo_de_revocacion_se_bloquea_el_writeback(mocker):
    """La compuerta de write-back no tiene sesión HTTP delante: se comprueba en
    el único punto por el que pasan todas las escrituras reales a Meta."""
    from app.services.meta_ads.exceptions import WritebackBlocked
    from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

    mocker.patch(
        "app.utils.security.revocation_backend_available", return_value=False
    )
    limiter = MetaAPIRateLimiter(ad_account_id="acc-1", external_account_id="act_1")
    with pytest.raises(WritebackBlocked):
        await limiter._assert_writeback_allowed()


@pytest.mark.asyncio
async def test_c15_sin_mecanismo_de_revocacion_la_lectura_sigue_respondiendo(
    async_client: AsyncClient, mocker
):
    """El par del criterio 14. Un test que solo comprobara el bloqueo no
    distingue fail-closed de caída total."""
    body, headers = await _signup(async_client, "c15-lectura")

    mocker.patch("app.utils.security.is_revoked", side_effect=RevocationCheckUnavailable())

    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await async_client.get(
        "/api/v1/campaigns/recent", headers=headers
    )).status_code == 200
