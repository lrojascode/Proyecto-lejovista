"""Tests para aviso anticipado de expiración de token de Meta (Fase 6.4 / Roadmap 7)."""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_credential import MetaCredential
from app.services.meta_ads.token_inspection import (
    inspect_token_expiration,
    check_expiring_tokens,
    WARNING_THRESHOLD_DAYS,
)


@pytest.mark.asyncio
async def test_inspect_token_expiration_logic():
    # 1. Token normal
    res_normal = await inspect_token_expiration("EAABsbCS12345normaltoken")
    assert res_normal["is_valid"] is True
    assert res_normal["expires_at"] is not None
    assert res_normal["error"] is None

    # 2. Token por vencer (< 7 días)
    res_expiring = await inspect_token_expiration("EAAB_expiring_token_soon")
    assert res_expiring["is_valid"] is True
    days_left = (res_expiring["expires_at"] - datetime.datetime.utcnow()).days
    assert days_left <= WARNING_THRESHOLD_DAYS

    # 3. Token expirado / revocado
    res_expired = await inspect_token_expiration("EAAB_expired_invalid_token")
    assert res_expired["is_valid"] is False
    assert "190" in (res_expired["error"] or "")


@pytest.mark.asyncio
async def test_check_expiring_tokens_service(async_client: AsyncClient, db_session: AsyncSession, mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    mocker.patch("app.services.meta_ads.token_inspection.NotificationService.notify_job_status")
    mocker.patch("app.services.meta_ads.token_inspection._notify_admins_by_email")

    # Signup org
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "admin@warning-test.com",
        "password": "securepassword123",
        "full_name": "Admin Warning",
        "organization_slug": "warning-test-org",
        "organization_name": "Warning Org",
    })
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": "warning-test-org"}

    # Conectar cuenta con token normal
    acc1_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json={
        "platform": "META",
        "name": "Cuenta Normal",
        "external_account_id": "act_111111",
        "access_token": "valid_token_normal_12345",
    }, headers=headers)
    assert acc1_res.status_code == 200

    # Conectar cuenta con token que vence pronto
    acc2_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json={
        "platform": "META",
        "name": "Cuenta Por Vencer",
        "external_account_id": "act_222222",
        "access_token": "expiring_token_in_3_days",
    }, headers=headers)
    assert acc2_res.status_code == 200

    # Ejecutar servicio de chequeo diario
    results = await check_expiring_tokens(db_session)
    assert len(results) >= 2

    # Verificar que Cuenta Por Vencer fue detectada con warning
    por_vencer = [r for r in results if r["name"] == "Cuenta Por Vencer"][0]
    assert por_vencer["warning"] is True
    assert por_vencer["status"] == "EXPIRING_SOON"
    assert por_vencer["days_left"] <= WARNING_THRESHOLD_DAYS


@pytest.mark.asyncio
async def test_check_token_endpoint(async_client: AsyncClient, db_session: AsyncSession, mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    mocker.patch("app.services.meta_ads.token_inspection.NotificationService.notify_job_status")

    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "admin@endpoint-check.com",
        "password": "securepassword123",
        "full_name": "Admin Check",
        "organization_slug": "endpoint-check-org",
        "organization_name": "Endpoint Check Org",
    })
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": "endpoint-check-org"}

    acc_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json={
        "platform": "META",
        "name": "Cuenta Check",
        "external_account_id": "act_333333",
        "access_token": "expiring_token_check",
    }, headers=headers)
    acc_id = acc_res.json()["id"]

    # Invocar endpoint manual de check-token
    check_res = await async_client.post(
        f"/api/v1/organizations/my/ad-accounts/{acc_id}/check-token",
        headers=headers,
    )
    assert check_res.status_code == 200
    data = check_res.json()
    assert data["token_warning"] is True
    assert data["token_expires_in_days"] is not None
    assert data["token_expires_in_days"] <= 7
