"""Tests de la Fase 1 del plan de creación de campañas: el endpoint de estado
real del CampaignDraft (antes el frontend solo simulaba el polling)."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Draft Tester", "organization_slug": slug, "organization_name": "Draft Org",
    })
    body = res.json()
    return body["access_token"], body["user"]["organization_id"]


@pytest.mark.asyncio
async def test_get_campaign_draft_status_requires_auth(async_client: AsyncClient):
    res = await async_client.get("/api/v1/campaigns/drafts/some-id")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_get_campaign_draft_returns_real_progress(async_client: AsyncClient, db_session: AsyncSession):
    token, org_id = await _signup(async_client, "draft-status-org")

    draft = CampaignDraft(
        organization_id=org_id,
        name="CableGPT",
        payload={
            "product_name": "CableGPT",
            "result": {"campaign_id": "mock_camp_1", "adset_id": "mock_adset_1"},
        },
        status="PENDING_CREATION",
    )
    db_session.add(draft)
    await db_session.commit()

    res = await async_client.get(
        f"/api/v1/campaigns/drafts/{draft.id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "PENDING_CREATION"
    assert body["payload"]["result"]["campaign_id"] == "mock_camp_1"
    assert body["error_message"] is None


@pytest.mark.asyncio
async def test_get_campaign_draft_404_for_other_tenant(async_client: AsyncClient, db_session: AsyncSession):
    _, org_a_id = await _signup(async_client, "draft-org-a")
    token_b, _ = await _signup(async_client, "draft-org-b")

    draft = CampaignDraft(
        organization_id=org_a_id, name="Ajeno", payload={}, status="CREATED",
    )
    db_session.add(draft)
    await db_session.commit()

    res = await async_client.get(
        f"/api/v1/campaigns/drafts/{draft.id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_campaign_draft_404_when_missing(async_client: AsyncClient):
    token, _ = await _signup(async_client, "draft-missing-org")
    res = await async_client.get(
        "/api/v1/campaigns/drafts/does-not-exist", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 404
