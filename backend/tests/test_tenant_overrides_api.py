"""Tests para API de configuración por empresa de reglas de campaña y umbrales de IA (Fase 6.3 / Roadmap 6)."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _signup_admin(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.com",
        "password": "securepassword123",
        "full_name": "Admin User",
        "organization_slug": slug,
        "organization_name": f"Org {slug}",
    })
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": slug}


@pytest.mark.asyncio
async def test_campaign_rules_api_lifecycle_and_validation(async_client: AsyncClient, db_session: AsyncSession):
    headers = await _signup_admin(async_client, "rules-api-test")

    # 1. Obtener reglas iniciales (defaults)
    get_res = await async_client.get("/api/v1/organizations/my/campaign-rules", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["rules"]["optimization_goal"] == "CONVERSATIONS"
    assert data["rules"]["default_status"] == "PAUSED"
    assert data["overrides"] == {}

    # 2. Intentar guardar optimization_goal inválido -> 400
    bad_res = await async_client.put(
        "/api/v1/organizations/my/campaign-rules",
        json={"overrides": {"optimization_goal": "INVALID_GOAL_XYZ"}},
        headers=headers,
    )
    assert bad_res.status_code == 400
    assert "inválido" in bad_res.json()["detail"]

    # 3. Guardar override válido
    ok_res = await async_client.put(
        "/api/v1/organizations/my/campaign-rules",
        json={"overrides": {
            "optimization_goal": "LEAD_GENERATION",
            "max_campaigns_per_window": 8,
            "default_status": "ACTIVE",  # Debe ser ignorado/bloqueado
        }},
        headers=headers,
    )
    assert ok_res.status_code == 200
    updated = ok_res.json()
    assert updated["rules"]["optimization_goal"] == "LEAD_GENERATION"
    assert updated["rules"]["max_campaigns_per_window"] == 8
    assert updated["rules"]["default_status"] == "PAUSED"  # Sigue PAUSED
    assert "default_status" not in updated["overrides"]


@pytest.mark.asyncio
async def test_ai_thresholds_api_lifecycle_and_validation(async_client: AsyncClient, db_session: AsyncSession):
    headers = await _signup_admin(async_client, "thresholds-api-test")

    # 1. Obtener umbrales iniciales
    get_res = await async_client.get("/api/v1/organizations/my/ai-thresholds", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["thresholds"]["cpl_critical_multiplier"] == 2.0
    assert data["overrides"] == {}

    # 2. Intentar guardar campo inexistente -> 400
    bad_res = await async_client.put(
        "/api/v1/organizations/my/ai-thresholds",
        json={"overrides": {"invented_threshold_key": 99.0}},
        headers=headers,
    )
    assert bad_res.status_code == 400
    assert "desconocido" in bad_res.json()["detail"]

    # 3. Guardar override válido
    ok_res = await async_client.put(
        "/api/v1/organizations/my/ai-thresholds",
        json={"overrides": {
            "cpl_critical_multiplier": 1.5,
            "target_cpl_fallback": 5.0,
        }},
        headers=headers,
    )
    assert ok_res.status_code == 200
    updated = ok_res.json()
    assert updated["thresholds"]["cpl_critical_multiplier"] == 1.5
    assert updated["thresholds"]["target_cpl_fallback"] == 5.0
    assert updated["overrides"]["cpl_critical_multiplier"] == 1.5


@pytest.mark.asyncio
async def test_default_status_no_es_overrideable_por_tenant(async_client: AsyncClient):
    """La regla de oro del playbook: toda campaña nace PAUSED y la activación es
    manual. Un tenant no puede desactivarla por configuración — el endpoint
    descarta `default_status`/`forbidden_status` en silencio en vez de guardarlos.
    """
    headers = await _signup_admin(async_client, "org-regla-de-oro")

    res = await async_client.put(
        "/api/v1/organizations/my/campaign-rules",
        json={"overrides": {
            "default_status": "ACTIVE",
            "forbidden_status": "PAUSED",
            "max_campaigns_per_window": 20,
        }},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()

    # Las dos claves prohibidas no se guardaron...
    assert "default_status" not in body["overrides"]
    assert "forbidden_status" not in body["overrides"]
    # ...y las reglas efectivas siguen creando en PAUSED.
    assert body["rules"]["default_status"] == "PAUSED"
    assert body["rules"]["forbidden_status"] == "ACTIVE"
    # El override legítimo del mismo payload sí se aplicó.
    assert body["overrides"]["max_campaigns_per_window"] == 20
    assert body["rules"]["max_campaigns_per_window"] == 20
