"""Página predeterminada por cuenta + refresh de páginas bajo demanda +
intercambio a long-lived token (feature "elegir página de Facebook para la
campaña", 15-jul-2026, caso Althea pack)."""
import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.services.meta_ads.token_exchange import exchange_for_long_lived_token
from conftest import enlazar_paginas_y_numeros


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Default Page Tester", "organization_slug": slug,
        "organization_name": "Default Page Org",
    })
    body = res.json()
    return body["access_token"], body["user"]["organization_id"]


async def _connect_account(async_client: AsyncClient, token: str, ext_id="act_777"):
    res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={"platform": "META", "external_account_id": ext_id,
              "name": "Cuenta Test", "access_token": "short-lived-token"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    return res.json()


# ------------------------------------------------ default page endpoint

@pytest.mark.asyncio
async def test_set_default_page_requires_auth(async_client: AsyncClient):
    res = await async_client.patch(
        "/api/v1/organizations/my/ad-accounts/some-id/default-page", json={"page_id": None}
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_set_and_clear_default_page(async_client: AsyncClient, db_session: AsyncSession, mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    token, org_id = await _signup(async_client, "default-page-org")
    acc = await _connect_account(async_client, token)
    page = MetaPage(organization_id=org_id, external_page_id="page_1",
                    name="Althea pack", whatsapp_number="51999888777", status="ACTIVE")
    db_session.add(page)
    await db_session.flush()
    # El ALCANCE de una página es por cuenta, no por organización: se replica
    # lo que en producción hace el sync (o el seed de la migración).
    await enlazar_paginas_y_numeros(db_session, org_id)
    await db_session.commit()

    res = await async_client.patch(
        f"/api/v1/organizations/my/ad-accounts/{acc['id']}/default-page",
        json={"page_id": page.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["default_page_id"] == page.id

    # También se refleja en el listado de cuentas.
    listed = await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers={"Authorization": f"Bearer {token}"}
    )
    assert listed.json()[0]["default_page_id"] == page.id

    # Limpiar con null.
    res = await async_client.patch(
        f"/api/v1/organizations/my/ad-accounts/{acc['id']}/default-page",
        json={"page_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["default_page_id"] is None


@pytest.mark.asyncio
async def test_set_default_page_rejects_foreign_page(async_client: AsyncClient, db_session: AsyncSession, mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    _, org_a_id = await _signup(async_client, "default-page-org-a")
    token_b, _ = await _signup(async_client, "default-page-org-b")
    acc_b = await _connect_account(async_client, token_b, ext_id="act_888")

    foreign_page = MetaPage(organization_id=org_a_id, external_page_id="page_ajena",
                            name="Ajena", status="ACTIVE")
    db_session.add(foreign_page)
    await db_session.commit()

    res = await async_client.patch(
        f"/api/v1/organizations/my/ad-accounts/{acc_b['id']}/default-page",
        json={"page_id": foreign_page.id},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res.status_code == 404


# ------------------------------------------------ pages refresh endpoint

@pytest.mark.asyncio
async def test_refresh_pages_requires_auth(async_client: AsyncClient):
    res = await async_client.post("/api/v1/organizations/my/pages/refresh")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_refresh_pages_enqueues_task(async_client: AsyncClient, db_session: AsyncSession, mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    mock_refresh = mocker.patch("app.api.v1.organizations.refresh_org_pages")
    token, org_id = await _signup(async_client, "refresh-pages-org")
    await _connect_account(async_client, token)

    res = await async_client.post(
        "/api/v1/organizations/my/pages/refresh", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert "1 cuentas" in res.json()["message"]
    mock_refresh.delay.assert_called_once_with(org_id)


@pytest.mark.asyncio
async def test_refresh_pages_400_without_active_accounts(async_client: AsyncClient, mocker):
    mock_refresh = mocker.patch("app.api.v1.organizations.refresh_org_pages")
    token, _ = await _signup(async_client, "refresh-pages-empty-org")

    res = await async_client.post(
        "/api/v1/organizations/my/pages/refresh", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 400
    mock_refresh.delay.assert_not_called()


# ------------------------------------------------ token exchange

@pytest.mark.asyncio
async def test_exchange_noop_with_mock_app_credentials():
    """Con META_APP_ID/SECRET mock (el default), el intercambio no aplica —
    conectar una cuenta guarda el token original sin llamar a Meta."""
    token, expires = await exchange_for_long_lived_token("short-lived")
    assert token is None and expires is None


@pytest.mark.asyncio
async def test_exchange_returns_long_lived_token(mocker):
    mocker.patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "META_APP_ID", "1499133071388192",
    )
    mocker.patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "META_APP_SECRET", "real-secret",
    )
    mocker.patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "MOCK_SERVICES", False,
    )

    class _FakeResponse:
        def json(self):
            return {"access_token": "long-lived-token", "token_type": "bearer",
                    "expires_in": 5183944}

    class _FakeClient:
        def __init__(self, *args, **kwargs): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, params=None):
            assert params["grant_type"] == "fb_exchange_token"
            assert params["fb_exchange_token"] == "short-lived"
            return _FakeResponse()

    mocker.patch("app.services.meta_ads.token_exchange.httpx.AsyncClient", _FakeClient)

    token, expires = await exchange_for_long_lived_token("short-lived")
    assert token == "long-lived-token"
    # ~60 días de vigencia.
    assert expires > datetime.datetime.utcnow() + datetime.timedelta(days=55)


@pytest.mark.asyncio
async def test_exchange_failure_falls_back_to_original_token_on_connect(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """Si el intercambio falla/no aplica, conectar la cuenta guarda el token
    original — la conexión nunca se bloquea por el intercambio."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    token, org_id = await _signup(async_client, "exchange-fallback-org")
    acc_json = await _connect_account(async_client, token)

    acc = (await db_session.execute(
        select(AdAccount).where(AdAccount.id == acc_json["id"])
    )).scalar_one()
    assert acc.get_access_token() == "short-lived-token"


@pytest.mark.asyncio
async def test_connect_stores_exchanged_token_when_exchange_succeeds(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=60)
    mocker.patch(
        "app.api.v1.organizations.exchange_for_long_lived_token",
        return_value=("exchanged-long-lived", expires),
    )
    token, _ = await _signup(async_client, "exchange-ok-org")
    acc_json = await _connect_account(async_client, token, ext_id="act_999")

    acc = (await db_session.execute(
        select(AdAccount).where(AdAccount.id == acc_json["id"])
    )).scalar_one()
    assert acc.get_access_token() == "exchanged-long-lived"
    assert acc.meta_credential.token_expires_at.date() == expires.date()
