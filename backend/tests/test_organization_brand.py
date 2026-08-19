"""Tests para marca por organización (Fase 6.2 / Roadmap 5)."""
import io
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _signup_org(async_client: AsyncClient, slug: str, name: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.com",
        "password": "securepassword123",
        "full_name": "Admin User",
        "organization_slug": slug,
        "organization_name": name,
    })
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": slug}


@pytest.mark.asyncio
async def test_update_org_brand_settings(async_client: AsyncClient, db_session: AsyncSession):
    headers = await _signup_org(async_client, "brand-test-1", "Agencia Alpha")

    # Actualizar display_name y accent_color
    patch_res = await async_client.patch(
        "/api/v1/organizations/my",
        json={
            "display_name": "Alpha Marketing Global",
            "accent_color": "#3B82F6",
        },
        headers=headers,
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["display_name"] == "Alpha Marketing Global"
    assert data["accent_color"] == "#3B82F6"


@pytest.mark.asyncio
async def test_upload_org_logo(async_client: AsyncClient, db_session: AsyncSession, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))

    headers = await _signup_org(async_client, "brand-logo-test", "Agencia Logo")

    # Sube un archivo PNG válido
    png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    files = {"file": ("logo.png", io.BytesIO(png_content), "image/png")}

    upload_res = await async_client.post(
        "/api/v1/organizations/my/logo",
        files=files,
        headers=headers,
    )
    assert upload_res.status_code == 200
    logo_url = upload_res.json()["logo_url"]
    assert logo_url.startswith("/media/logos/")
    assert logo_url.endswith(".png")

    # Comprobar que GET /organizations/my ahora incluye el logo_url
    my_res = await async_client.get("/api/v1/organizations/my", headers=headers)
    assert my_res.status_code == 200
    assert my_res.json()["logo_url"] == logo_url


@pytest.mark.asyncio
async def test_tenant_brand_public_endpoint_isolation(async_client: AsyncClient, db_session: AsyncSession):
    headers_org1 = await _signup_org(async_client, "client-a", "Cliente A Org")
    await async_client.patch(
        "/api/v1/organizations/my",
        json={"display_name": "Cliente A Personalizado", "accent_color": "#10B981"},
        headers=headers_org1,
    )

    # 1. Consultar tenant existente vía query param
    res_a = await async_client.get("/api/v1/auth/tenant-brand?slug=client-a")
    assert res_a.status_code == 200
    brand_a = res_a.json()
    assert brand_a["name"] == "Cliente A Org"
    assert brand_a["display_name"] == "Cliente A Personalizado"
    assert brand_a["accent_color"] == "#10B981"

    # 2. Consultar sin slug o con slug inexistente -> devuelve marca neutra LejoVista
    res_neutral = await async_client.get("/api/v1/auth/tenant-brand?slug=non-existent-tenant")
    assert res_neutral.status_code == 200
    brand_n = res_neutral.json()
    assert brand_n["name"] == "LejoVista"
    assert brand_n["display_name"] == "LejoVista"
    assert brand_n["logo_url"] is None

    # 3. Consultar slug reservado -> marca neutra
    res_res = await async_client.get("/api/v1/auth/tenant-brand?slug=api")
    assert res_res.status_code == 200
    assert res_res.json()["name"] == "LejoVista"
