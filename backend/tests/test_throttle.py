"""Tests de app/utils/throttle.py — cooldown existente y el nuevo rate limiter
por IP (agregado para exponer la app vía túnel a un socio de prueba)."""
import pytest
from fastapi import HTTPException
from httpx import AsyncClient


class _FakeRedis:
    """Redis en memoria suficiente para enforce_rate_limit/enforce_cooldown.
    No simula expiración real (los tests que la necesitan la fuerzan a mano)."""
    store: dict = {}
    ttls: dict = {}

    def __init__(self, *a, **kw):
        pass

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def get(self, key):
        return self.store.get(key)

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _reset_fake_redis():
    _FakeRedis.store = {}
    _FakeRedis.ttls = {}
    yield


@pytest.fixture
def fake_redis(mocker):
    mocker.patch("app.utils.throttle.redis.Redis.from_url", return_value=_FakeRedis())


@pytest.mark.asyncio
async def test_enforce_rate_limit_blocks_after_max_attempts(fake_redis):
    from app.utils.throttle import enforce_rate_limit

    for _ in range(3):
        await enforce_rate_limit("test-action", "1.2.3.4", max_attempts=3, window_seconds=60)

    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limit("test-action", "1.2.3.4", max_attempts=3, window_seconds=60)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_enforce_rate_limit_is_scoped_per_key(fake_redis):
    """Una IP distinta no debería verse afectada por el límite de otra."""
    from app.utils.throttle import enforce_rate_limit

    for _ in range(3):
        await enforce_rate_limit("test-action", "1.1.1.1", max_attempts=3, window_seconds=60)
    # Otra IP: no debe estar bloqueada todavía.
    await enforce_rate_limit("test-action", "2.2.2.2", max_attempts=3, window_seconds=60)


@pytest.mark.asyncio
async def test_enforce_rate_limit_fails_open_without_redis(mocker):
    from app.utils.throttle import enforce_rate_limit

    class _BrokenRedis(_FakeRedis):
        async def incr(self, key):
            raise ConnectionError("no redis")

    mocker.patch("app.utils.throttle.redis.Redis.from_url", return_value=_BrokenRedis())
    # No debe lanzar excepción: sin Redis disponible (falla en el primer
    # comando, como redis-py real — from_url no conecta eagerly), se permite
    # la acción en vez de tumbar el login/signup.
    await enforce_rate_limit("test-action", "3.3.3.3", max_attempts=1, window_seconds=60)


# --- Slugs de organización reservados (colisionan con server_name de Nginx) ---
@pytest.mark.asyncio
async def test_signup_rejects_reserved_org_slug(async_client: AsyncClient):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "reserved-slug@example.com", "password": "securepassword123",
        "full_name": "Reserved Slug User", "organization_slug": "api",
        "organization_name": "Debería fallar",
    })
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_signup_rejects_short_password(async_client: AsyncClient):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "shortpass@example.com", "password": "1234567",
        "full_name": "Short Pass User", "organization_slug": "short-pass-org",
        "organization_name": "Short Pass Org",
    })
    assert res.status_code == 422
