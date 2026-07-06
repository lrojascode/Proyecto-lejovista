"""Tests del soft-delete de cuentas publicitarias (HANDOFF §10): en vez de
borrar de verdad (irreversible, y api_call_logs no tiene ondelete configurado
— fallaría), se archiva vía status=ARCHIVED. Debe desaparecer de las listas
normales y de la auto-selección, pero conservar todo su historial."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount


@pytest.fixture(autouse=True)
def _mock_sync(mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Archive Tester", "organization_slug": slug, "organization_name": "Archive Org",
    })
    body = res.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["organization_id"]


async def _connect_account(async_client: AsyncClient, headers, external_id="act_111"):
    res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={"platform": "META", "external_account_id": external_id, "name": "Cuenta Test", "access_token": "tok"},
        headers=headers,
    )
    return res.json()["id"]


@pytest.mark.asyncio
async def test_archive_requires_auth(async_client: AsyncClient):
    res = await async_client.post("/api/v1/organizations/my/ad-accounts/some-id/archive")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_archive_hides_account_from_default_list(async_client: AsyncClient):
    headers, _ = await _signup(async_client, "archive-org-1")
    acc_id = await _connect_account(async_client, headers)

    archive_res = await async_client.post(f"/api/v1/organizations/my/ad-accounts/{acc_id}/archive", headers=headers)
    assert archive_res.status_code == 200
    assert archive_res.json()["status"] == "ARCHIVED"

    list_res = await async_client.get("/api/v1/organizations/my/ad-accounts", headers=headers)
    assert list_res.json() == []

    list_all_res = await async_client.get(
        "/api/v1/organizations/my/ad-accounts?include_archived=true", headers=headers
    )
    assert len(list_all_res.json()) == 1
    assert list_all_res.json()[0]["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_restore_brings_account_back(async_client: AsyncClient):
    headers, _ = await _signup(async_client, "archive-org-2")
    acc_id = await _connect_account(async_client, headers)
    await async_client.post(f"/api/v1/organizations/my/ad-accounts/{acc_id}/archive", headers=headers)

    restore_res = await async_client.post(f"/api/v1/organizations/my/ad-accounts/{acc_id}/restore", headers=headers)
    assert restore_res.status_code == 200
    assert restore_res.json()["status"] == "ACTIVE"

    list_res = await async_client.get("/api/v1/organizations/my/ad-accounts", headers=headers)
    assert len(list_res.json()) == 1


@pytest.mark.asyncio
async def test_restore_fails_if_not_archived(async_client: AsyncClient):
    headers, _ = await _signup(async_client, "archive-org-3")
    acc_id = await _connect_account(async_client, headers)

    res = await async_client.post(f"/api/v1/organizations/my/ad-accounts/{acc_id}/restore", headers=headers)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_archive_404_for_other_tenant(async_client: AsyncClient):
    headers_a, _ = await _signup(async_client, "archive-org-a")
    headers_b, _ = await _signup(async_client, "archive-org-b")
    acc_id = await _connect_account(async_client, headers_a)

    res = await async_client.post(f"/api/v1/organizations/my/ad-accounts/{acc_id}/archive", headers=headers_b)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_archived_account_not_selectable_via_header(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "archive-org-header")
    acc_id = await _connect_account(async_client, headers, external_id="act_222")
    await async_client.post(f"/api/v1/organizations/my/ad-accounts/{acc_id}/archive", headers=headers)

    res = await async_client.get(
        "/api/v1/campaigns/recent", headers={**headers, "X-Ad-Account-Id": acc_id}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_archived_account_excluded_from_single_account_auto_select(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Con 1 sola cuenta ACTIVA + 1 archivada, se sigue auto-seleccionando la
    activa (no cuenta como "2 cuentas ambiguas")."""
    headers, org_id = await _signup(async_client, "archive-org-autoselect")
    active_id = await _connect_account(async_client, headers, external_id="act_active")
    archived_id = await _connect_account(async_client, headers, external_id="act_archived")
    await async_client.post(f"/api/v1/organizations/my/ad-accounts/{archived_id}/archive", headers=headers)

    res = await async_client.get("/api/v1/campaigns/recent", headers=headers)
    assert res.status_code == 200  # no 400 MULTIPLE_AD_ACCOUNTS_NO_SELECTION

    # Verificación directa: solo queda 1 cuenta activa.
    accounts = (await db_session.execute(
        select(AdAccount).where(
            AdAccount.organization_id == org_id, AdAccount.status == "ACTIVE"
        )
    )).scalars().all()
    assert len(accounts) == 1
    assert accounts[0].id == active_id
