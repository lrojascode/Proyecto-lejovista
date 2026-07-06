"""Tests de la Fase 3C: GET /campaigns/suggestion une CampaignSuggester (3A)
y OpenRouterClient.suggest_campaign_copy (3B) para pre-rellenar el formulario
de creación de campañas."""
import pytest
from httpx import AsyncClient


async def _signup_with_ad_account(async_client: AsyncClient, slug: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Suggestion Tester", "organization_slug": slug, "organization_name": "Suggestion Org",
    })
    body = signup_res.json()
    token = body["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    acc_res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={
            "platform": "META", "external_account_id": "act_1",
            "name": "Suggestion Acc", "access_token": "mock-token",
        },
        headers=headers,
    )
    return headers, acc_res.json()["id"]


@pytest.fixture(autouse=True)
def _mock_ad_account_sync(mocker):
    # Conectar una cuenta dispara sync_ad_account.delay(...) real (ver
    # test_campaign_creation_endpoint.py) — se mockea para no requerir Redis.
    mocker.patch("app.api.v1.organizations.sync_ad_account")


@pytest.mark.asyncio
async def test_suggestion_endpoint_requires_auth(async_client: AsyncClient):
    res = await async_client.get("/api/v1/campaigns/suggestion?ad_account_id=x")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_suggestion_endpoint_returns_structure_and_copy(async_client: AsyncClient):
    headers, ad_account_id = await _signup_with_ad_account(async_client, "suggest-endpoint-org")

    res = await async_client.get(
        f"/api/v1/campaigns/suggestion?ad_account_id={ad_account_id}&product_name=CableGPT&zone=Surquillo",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()

    assert body["risk_level"] == "MEDIUM"
    assert body["has_history"] is False  # cuenta recién conectada, sin snapshots
    assert body["objective"] == "OUTCOME_LEADS"
    assert body["destination_type"] == "WHATSAPP"
    assert body["suggested_budget"] == 100.0
    assert "CableGPT" in body["copy"]["headline"]
    assert "Surquillo" in body["copy"]["headline"]
    assert set(body["copy"].keys()) == {"headline", "primary_text", "description", "welcome_message"}


@pytest.mark.asyncio
async def test_suggestion_endpoint_404_for_other_tenant_ad_account(async_client: AsyncClient):
    _, ad_account_a = await _signup_with_ad_account(async_client, "suggest-org-a")
    headers_b, _ = await _signup_with_ad_account(async_client, "suggest-org-b")

    res = await async_client.get(
        f"/api/v1/campaigns/suggestion?ad_account_id={ad_account_a}", headers=headers_b
    )
    assert res.status_code == 404
