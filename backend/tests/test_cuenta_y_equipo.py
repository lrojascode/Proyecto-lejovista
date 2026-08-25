"""Lo que un cliente no podía resolver solo.

No había forma de cambiar la contraseña, ni de recuperarla si la perdía (la
única salida era editar el hash en la base), ni de quitar a alguien del equipo,
ni de cambiarle el rol, ni de corregir el propio nombre o correo.
"""
import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.password_reset import PasswordResetToken
from app.models.user import User


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def fake_redis(mocker):
    mocker.patch("app.utils.security.aioredis.Redis.from_url", return_value=_FakeRedis())


async def _signup(async_client: AsyncClient, slug: str, email: str = None):
    email = email or f"{slug}@example.com"
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123", "full_name": "Titular",
        "organization_slug": slug, "organization_name": "Org",
    })
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}, email


# ----------------------------------------------------------------- contraseña

@pytest.mark.asyncio
async def test_cambiar_la_contrasena_y_entrar_con_la_nueva(async_client: AsyncClient):
    headers, email = await _signup(async_client, "pass-cambio")

    res = await async_client.post("/api/v1/auth/change-password", headers=headers, json={
        "current_password": "securepassword123", "new_password": "otraclavesegura456",
    })
    assert res.status_code == 200

    vieja = await async_client.post("/api/v1/auth/login", json={
        "email": email, "password": "securepassword123",
    })
    assert vieja.status_code == 401

    nueva = await async_client.post("/api/v1/auth/login", json={
        "email": email, "password": "otraclavesegura456",
    })
    assert nueva.status_code == 200


@pytest.mark.asyncio
async def test_sin_la_contrasena_actual_no_se_cambia(async_client: AsyncClient):
    """Una sesión robada no debe alcanzar para cambiar la clave y dejar afuera
    al dueño."""
    headers, _ = await _signup(async_client, "pass-actual")

    res = await async_client.post("/api/v1/auth/change-password", headers=headers, json={
        "current_password": "la-que-no-es", "new_password": "otraclavesegura456",
    })
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_la_contrasena_nueva_tiene_minimo(async_client: AsyncClient):
    headers, _ = await _signup(async_client, "pass-corta")
    res = await async_client.post("/api/v1/auth/change-password", headers=headers, json={
        "current_password": "securepassword123", "new_password": "corta",
    })
    assert res.status_code == 422


# --------------------------------------------------------------- recuperación

@pytest.mark.asyncio
async def test_recuperar_la_contrasena_de_punta_a_punta(
    async_client: AsyncClient, db_session: AsyncSession
):
    _, email = await _signup(async_client, "pass-recupero")

    pedido = await async_client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert pedido.status_code == 200

    fila = (await db_session.execute(select(PasswordResetToken))).scalars().one()
    # El token viaja por correo; lo que se guarda es su hash, nunca el valor.
    assert len(fila.token_hash) == 64
    assert fila.is_usable

    # El valor original no se persiste: se reconstruye el hash para el test.
    import hashlib
    from app.api.v1.auth import _hash_reset_token

    token = "token-de-prueba"
    fila.token_hash = _hash_reset_token(token)
    await db_session.commit()
    assert fila.token_hash == hashlib.sha256(token.encode()).hexdigest()

    res = await async_client.post("/api/v1/auth/reset-password", json={
        "token": token, "new_password": "clavereciensalida789",
    })
    assert res.status_code == 200

    entrar = await async_client.post("/api/v1/auth/login", json={
        "email": email, "password": "clavereciensalida789",
    })
    assert entrar.status_code == 200


@pytest.mark.asyncio
async def test_el_enlace_de_recuperacion_sirve_una_sola_vez(
    async_client: AsyncClient, db_session: AsyncSession
):
    from app.api.v1.auth import _hash_reset_token

    _, email = await _signup(async_client, "pass-unauso")
    await async_client.post("/api/v1/auth/forgot-password", json={"email": email})

    fila = (await db_session.execute(select(PasswordResetToken))).scalars().one()
    fila.token_hash = _hash_reset_token("token-unico")
    await db_session.commit()

    primero = await async_client.post("/api/v1/auth/reset-password", json={
        "token": "token-unico", "new_password": "clavereciensalida789",
    })
    assert primero.status_code == 200

    segundo = await async_client.post("/api/v1/auth/reset-password", json={
        "token": "token-unico", "new_password": "otraclavemas1234",
    })
    assert segundo.status_code == 400


@pytest.mark.asyncio
async def test_un_enlace_vencido_no_sirve(
    async_client: AsyncClient, db_session: AsyncSession
):
    from app.api.v1.auth import _hash_reset_token

    _, email = await _signup(async_client, "pass-vencido")
    await async_client.post("/api/v1/auth/forgot-password", json={"email": email})

    fila = (await db_session.execute(select(PasswordResetToken))).scalars().one()
    fila.token_hash = _hash_reset_token("token-viejo")
    fila.expires_at = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    await db_session.commit()

    res = await async_client.post("/api/v1/auth/reset-password", json={
        "token": "token-viejo", "new_password": "clavereciensalida789",
    })
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_pedir_uno_nuevo_invalida_el_anterior(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Un enlace viejo dando vueltas en una casilla es una llave viva."""
    _, email = await _signup(async_client, "pass-dos-pedidos")

    await async_client.post("/api/v1/auth/forgot-password", json={"email": email})
    await async_client.post("/api/v1/auth/forgot-password", json={"email": email})

    filas = (await db_session.execute(
        select(PasswordResetToken).order_by(PasswordResetToken.created_at)
    )).scalars().all()
    assert len(filas) == 2
    assert filas[0].is_used, "el primer enlace tenía que quedar invalidado"
    assert filas[1].is_usable


@pytest.mark.asyncio
async def test_un_email_inexistente_responde_igual(async_client: AsyncClient):
    """Distinguir los dos casos convierte el endpoint en un verificador de qué
    correos tienen cuenta."""
    _, email = await _signup(async_client, "pass-oraculo")

    existe = await async_client.post("/api/v1/auth/forgot-password", json={"email": email})
    no_existe = await async_client.post(
        "/api/v1/auth/forgot-password", json={"email": "nadie@example.com"}
    )
    assert existe.status_code == no_existe.status_code == 200
    assert existe.json() == no_existe.json()


# -------------------------------------------------------------------- perfil

@pytest.mark.asyncio
async def test_corregir_el_propio_nombre_y_correo(async_client: AsyncClient):
    headers, _ = await _signup(async_client, "perfil-edit")

    res = await async_client.patch("/api/v1/auth/me", headers=headers, json={
        "full_name": "Nombre Corregido", "email": "corregido@example.com",
    })
    assert res.status_code == 200
    assert res.json()["full_name"] == "Nombre Corregido"
    assert res.json()["email"] == "corregido@example.com"


@pytest.mark.asyncio
async def test_no_se_puede_tomar_el_correo_de_otro(async_client: AsyncClient):
    await _signup(async_client, "perfil-a", email="ocupado@example.com")
    headers, _ = await _signup(async_client, "perfil-b")

    res = await async_client.patch("/api/v1/auth/me", headers=headers, json={
        "email": "ocupado@example.com",
    })
    assert res.status_code == 400


# -------------------------------------------------------------------- equipo

async def _org_con_miembro(async_client: AsyncClient, db: AsyncSession, slug: str, rol="VIEWER"):
    headers, _ = await _signup(async_client, slug)
    org = (await db.execute(
        select(Organization).where(Organization.slug == slug)
    )).scalar_one()

    otro = User(email=f"otro-{slug}@example.com", hashed_password="x", full_name="Otro")
    db.add(otro)
    await db.flush()
    db.add(OrganizationUser(user_id=otro.id, organization_id=org.id, role=rol))
    await db.commit()
    return headers, org, otro


@pytest.mark.asyncio
async def test_cambiar_el_rol_de_un_miembro(async_client: AsyncClient, db_session: AsyncSession):
    headers, org, otro = await _org_con_miembro(async_client, db_session, "equipo-rol")

    res = await async_client.patch(
        f"/api/v1/organizations/my/members/{otro.id}", headers=headers, json={"role": "OPERATOR"}
    )
    assert res.status_code == 200

    miembros = await async_client.get("/api/v1/organizations/my/members", headers=headers)
    roles = {m["email"]: m["role"] for m in miembros.json()}
    assert roles[otro.email] == "OPERATOR"


@pytest.mark.asyncio
async def test_quitar_un_miembro_le_saca_tambien_las_cuentas(
    async_client: AsyncClient, db_session: AsyncSession
):
    from app.models.ad_account import AdAccount
    from app.models.user_ad_account import UserAdAccount

    headers, org, otro = await _org_con_miembro(async_client, db_session, "equipo-baja")
    acc = AdAccount(
        organization_id=org.id, platform="META", external_account_id="act_x",
        name="Cuenta", status="ACTIVE",
    )
    db_session.add(acc)
    await db_session.flush()
    db_session.add(UserAdAccount(
        user_id=otro.id, ad_account_id=acc.id, organization_id=org.id
    ))
    await db_session.commit()

    res = await async_client.delete(
        f"/api/v1/organizations/my/members/{otro.id}", headers=headers
    )
    assert res.status_code == 200

    quedan = (await db_session.execute(
        select(UserAdAccount).where(UserAdAccount.user_id == otro.id)
    )).scalars().all()
    assert quedan == [], "se fue de la organización pero conservó las cuentas"

    # El usuario NO se borra: puede ser miembro de otra organización.
    assert (await db_session.get(User, otro.id)) is not None


@pytest.mark.asyncio
async def test_no_puedo_quitarme_a_mi_mismo(async_client: AsyncClient, db_session: AsyncSession):
    headers, org, _ = await _org_con_miembro(async_client, db_session, "equipo-yo")
    yo = (await db_session.execute(
        select(User).where(User.email == "equipo-yo@example.com")
    )).scalar_one()

    res = await async_client.delete(
        f"/api/v1/organizations/my/members/{yo.id}", headers=headers
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_la_organizacion_no_se_queda_sin_administrador(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El último ADMIN degradándose deja la organización sin nadie que pueda
    invitar, conectar cuentas ni repartir accesos — y sin forma de arreglarlo."""
    headers, org, otro = await _org_con_miembro(async_client, db_session, "equipo-solo-admin")
    yo = (await db_session.execute(
        select(User).where(User.email == "equipo-solo-admin@example.com")
    )).scalar_one()

    res = await async_client.patch(
        f"/api/v1/organizations/my/members/{yo.id}", headers=headers, json={"role": "VIEWER"}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_no_se_toca_a_un_miembro_de_otra_organizacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers_a, _, _ = await _org_con_miembro(async_client, db_session, "equipo-org-a")
    _, _, ajeno = await _org_con_miembro(async_client, db_session, "equipo-org-b")

    rol = await async_client.patch(
        f"/api/v1/organizations/my/members/{ajeno.id}", headers=headers_a, json={"role": "ADMIN"}
    )
    assert rol.status_code == 404

    baja = await async_client.delete(
        f"/api/v1/organizations/my/members/{ajeno.id}", headers=headers_a
    )
    assert baja.status_code == 404


# --------------------------------------------------------------------- correo

@pytest.mark.asyncio
async def test_sin_proveedor_de_correo_el_envio_no_miente(mocker):
    """Devolvía `True` sin API key, igual que en modo mock. Los dos casos no son
    lo mismo: en mock no hay nada que enviar; sin API key en producción hay un
    correo que alguien está esperando y no va a llegar nunca. El panel decía
    "invitación enviada" y los avisos de token se daban por entregados."""
    from app.config import settings as app_settings
    from app.services.email_service import send_email, email_configurado

    mocker.patch.object(app_settings, "MOCK_SERVICES", False)
    mocker.patch.object(app_settings, "BREVO_API_KEY", "")

    assert await send_email("a@example.com", "Asunto", "<p>hola</p>") is False
    assert email_configurado() is False


@pytest.mark.asyncio
async def test_en_modo_mock_se_sigue_simulando_el_envio(mocker):
    from app.config import settings as app_settings
    from app.services.email_service import send_email, email_configurado

    mocker.patch.object(app_settings, "MOCK_SERVICES", True)
    assert await send_email("a@example.com", "Asunto", "<p>hola</p>") is True
    assert email_configurado() is True


@pytest.mark.asyncio
async def test_la_invitacion_dice_si_el_correo_salio(async_client: AsyncClient):
    headers, _ = await _signup(async_client, "invita-aviso")
    res = await async_client.post(
        "/api/v1/organizations/my/invitations",
        headers=headers, json={"email": "nuevo@example.com", "role": "VIEWER"},
    )
    assert res.status_code == 201
    # En tests MOCK_SERVICES=true, así que el envío se simula y sale True.
    assert res.json()["email_sent"] is True
    assert res.json()["invite_url"]
