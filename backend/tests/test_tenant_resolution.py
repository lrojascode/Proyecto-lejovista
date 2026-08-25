"""Con qué organización queda atada una sesión (S-8).

`login` elegía `user.organizations[0].organization_id`: sin `ORDER BY` y sin
mirar el subdominio, aunque el header viniera. Con un usuario que pertenece a
dos organizaciones, cuál quedaba en el token variaba entre sesiones — así es
como una API key terminó guardada en la organización equivocada y el analista
contestó con plantillas durante días sin que nada se viera roto.
"""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.utils.security import decode_token


class _FakeRedis:
    """Redis en memoria para revoke_jti/is_revoked (S-3). `is_revoked` es
    fail-CLOSED: sin Redis, /auth/refresh responde 503 en vez de renovar a
    ciegas. Mismo patrón que tests/test_audit_fixes.py."""
    def __init__(self):
        self.store: dict = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis(mocker):
    client = _FakeRedis()
    mocker.patch("app.utils.security.aioredis.Redis.from_url", return_value=client)
    return client


async def _usuario_en_dos_orgs(
    async_client: AsyncClient, db: AsyncSession, email: str, slug_a: str, slug_b: str
):
    """El usuario funda `slug_a` y después lo agregan a `slug_b`. La membresía
    de `slug_b` se crea con una fecha ANTERIOR para que "la primera fila" y "la
    más antigua" no coincidan: si el orden no fuera explícito, el test no
    distinguiría nada."""
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123", "full_name": "Multi",
        "organization_slug": slug_a, "organization_name": "Org A",
    })
    assert res.status_code == 200

    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    org_b = Organization(slug=slug_b, name="Org B", status="ACTIVE")
    db.add(org_b)
    await db.flush()
    db.add(OrganizationUser(
        user_id=user.id, organization_id=org_b.id, role="ADMIN",
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=365),
    ))
    await db.commit()

    org_a = (await db.execute(
        select(Organization).where(Organization.slug == slug_a)
    )).scalar_one()
    return user, org_a, org_b


@pytest.mark.asyncio
async def test_el_subdominio_decide_la_organizacion_del_token(
    async_client: AsyncClient, db_session: AsyncSession
):
    user, org_a, org_b = await _usuario_en_dos_orgs(
        async_client, db_session, "multi1@example.com", "tr-a1", "tr-b1"
    )

    res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "multi1@example.com", "password": "securepassword123"},
        headers={"X-Tenant-Slug": org_a.slug},
    )
    assert res.status_code == 200
    assert decode_token(res.json()["access_token"])["org"] == org_a.id
    assert res.json()["user"]["organization_id"] == org_a.id


@pytest.mark.asyncio
async def test_un_subdominio_ajeno_se_rechaza_en_el_login(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Emitir el token de otra organización y que cada request posterior
    devuelva 403 sin explicar por qué es peor que fallar acá."""
    await _usuario_en_dos_orgs(
        async_client, db_session, "multi2@example.com", "tr-a2", "tr-b2"
    )
    ajena = Organization(slug="tr-ajena", name="Ajena", status="ACTIVE")
    db_session.add(ajena)
    await db_session.commit()

    res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "multi2@example.com", "password": "securepassword123"},
        headers={"X-Tenant-Slug": "tr-ajena"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_sin_subdominio_la_organizacion_es_siempre_la_misma(
    async_client: AsyncClient, db_session: AsyncSession
):
    """No importa cuál gane, importa que no cambie entre sesiones."""
    user, org_a, org_b = await _usuario_en_dos_orgs(
        async_client, db_session, "multi3@example.com", "tr-a3", "tr-b3"
    )

    elegidas = set()
    for _ in range(3):
        res = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "multi3@example.com", "password": "securepassword123"},
        )
        assert res.status_code == 200
        elegidas.add(decode_token(res.json()["access_token"])["org"])

    assert len(elegidas) == 1
    # La membresía más antigua es la de org_b (se creó con fecha de hace un año).
    assert elegidas == {org_b.id}


@pytest.mark.asyncio
async def test_el_refresh_no_revive_una_organizacion_que_ya_no_es_tuya(
    async_client: AsyncClient, db_session: AsyncSession, fake_redis
):
    """El claim del refresh dura días. Si al usuario lo sacaron de esa
    organización, renovar la sesión contra ella sería seguir dándole acceso por
    inercia."""
    user, org_a, org_b = await _usuario_en_dos_orgs(
        async_client, db_session, "multi4@example.com", "tr-a4", "tr-b4"
    )

    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "multi4@example.com", "password": "securepassword123"},
        headers={"X-Tenant-Slug": org_a.slug},
    )
    assert decode_token(login.json()["access_token"])["org"] == org_a.id

    # Lo sacan de org_a.
    membresia = (await db_session.execute(
        select(OrganizationUser).where(
            OrganizationUser.user_id == user.id,
            OrganizationUser.organization_id == org_a.id,
        )
    )).scalar_one()
    await db_session.delete(membresia)
    await db_session.commit()

    res = await async_client.post("/api/v1/auth/refresh")
    assert res.status_code == 200
    assert decode_token(res.json()["access_token"])["org"] == org_b.id
