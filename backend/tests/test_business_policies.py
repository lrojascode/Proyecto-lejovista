"""Tests del rubro de negocio (Organization.industry) y las políticas de Meta
curadas que se muestran en el dashboard según ese rubro. Ver
app/services/policy_catalog.py — contenido fijo, no generado por IA."""
import pytest
from httpx import AsyncClient

from app.services.policy_catalog import INDUSTRIES, GENERIC_POLICIES, get_policies_for_industry


async def _signup(async_client: AsyncClient, slug: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Policy Tester", "organization_slug": slug, "organization_name": "Policy Org",
    })
    token = signup_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_get_policies_for_industry_without_industry_returns_only_generic():
    assert get_policies_for_industry(None) == GENERIC_POLICIES
    assert get_policies_for_industry("") == GENERIC_POLICIES


def test_get_policies_for_industry_includes_specific_policies():
    policies = get_policies_for_industry("salud_belleza")
    assert len(policies) > len(GENERIC_POLICIES)
    titles = [p["title"] for p in policies]
    assert "Reclamos de salud y resultados garantizados" in titles


def test_get_policies_for_industry_unknown_value_falls_back_to_generic():
    assert get_policies_for_industry("rubro_inventado") == GENERIC_POLICIES


@pytest.mark.asyncio
async def test_put_organization_saves_industry(async_client: AsyncClient):
    headers = await _signup(async_client, "policy-put-org")

    res = await async_client.put(
        "/api/v1/organizations/my", json={"industry": "finanzas"}, headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["industry"] == "finanzas"

    org_res = await async_client.get("/api/v1/organizations/my", headers=headers)
    assert org_res.json()["industry"] == "finanzas"


@pytest.mark.asyncio
async def test_policies_endpoint_requires_auth(async_client: AsyncClient):
    res = await async_client.get("/api/v1/organizations/my/policies")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_policies_endpoint_returns_generic_when_no_industry_set(async_client: AsyncClient):
    headers = await _signup(async_client, "policy-generic-org")

    res = await async_client.get("/api/v1/organizations/my/policies", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["industry"] is None
    assert body["policies"] == GENERIC_POLICIES
    assert body["available_industries"] == INDUSTRIES


@pytest.mark.asyncio
async def test_policies_endpoint_returns_specific_after_setting_industry(async_client: AsyncClient):
    headers = await _signup(async_client, "policy-specific-org")
    await async_client.put("/api/v1/organizations/my", json={"industry": "inmobiliaria"}, headers=headers)

    res = await async_client.get("/api/v1/organizations/my/policies", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["industry"] == "inmobiliaria"
    assert body["policies"] == get_policies_for_industry("inmobiliaria")
    assert len(body["policies"]) > len(GENERIC_POLICIES)
