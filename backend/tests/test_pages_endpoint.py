"""Tests de la Fase 4 del plan de creación de campañas: GET /organizations/my/pages
expone las páginas descubiertas por el sync (con WhatsApp) para el dropdown
del formulario de creación."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meta_page import MetaPage


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Pages Tester", "organization_slug": slug, "organization_name": "Pages Org",
    })
    body = res.json()
    return body["access_token"], body["user"]["organization_id"]


@pytest.mark.asyncio
async def test_list_my_pages_requires_auth(async_client: AsyncClient):
    res = await async_client.get("/api/v1/organizations/my/pages")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_list_my_pages_returns_active_pages_with_whatsapp(
    async_client: AsyncClient, db_session: AsyncSession
):
    token, org_id = await _signup(async_client, "pages-list-org")
    db_session.add(MetaPage(
        organization_id=org_id, external_page_id="page_1", name="Mi Página",
        whatsapp_number="51999888777", instagram_actor_id="ig_1", status="ACTIVE",
    ))
    db_session.add(MetaPage(
        organization_id=org_id, external_page_id="page_inactive", name="Inactiva",
        status="INACTIVE",
    ))
    await db_session.commit()

    res = await async_client.get(
        "/api/v1/organizations/my/pages", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["external_page_id"] == "page_1"
    assert body[0]["whatsapp_number"] == "51999888777"


@pytest.mark.asyncio
async def test_list_my_pages_scoped_per_organization(async_client: AsyncClient, db_session: AsyncSession):
    _, org_a_id = await _signup(async_client, "pages-org-a")
    token_b, _ = await _signup(async_client, "pages-org-b")

    db_session.add(MetaPage(
        organization_id=org_a_id, external_page_id="page_a", name="Ajena", status="ACTIVE",
    ))
    await db_session.commit()

    res = await async_client.get(
        "/api/v1/organizations/my/pages", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert res.status_code == 200
    assert res.json() == []
