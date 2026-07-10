"""Tests de la subida de imagen local para crear campañas (reemplaza pegar una
URL a mano): POST /campaigns/upload-image guarda el archivo en MEDIA_ROOT y
MetaAssetRepository.get_or_upload_image lo lee del disco compartido en vez de
hacer un round-trip HTTP."""
import os
import pytest
from httpx import AsyncClient

from app.services.meta_asset_repository import MetaAssetRepository


async def _signup(async_client: AsyncClient, slug: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Upload Tester", "organization_slug": slug, "organization_name": "Upload Org",
    })
    token = signup_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_upload_image_requires_auth(async_client: AsyncClient):
    res = await async_client.post(
        "/api/v1/campaigns/upload-image",
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_upload_image_saves_file_and_returns_path(async_client: AsyncClient, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    headers = await _signup(async_client, "upload-org")

    res = await async_client.post(
        "/api/v1/campaigns/upload-image",
        headers=headers,
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")},
    )
    assert res.status_code == 200
    image_path = res.json()["image_path"]
    assert image_path.endswith(".jpg")

    full_path = os.path.join(str(tmp_path), image_path)
    assert os.path.exists(full_path)
    with open(full_path, "rb") as f:
        assert f.read() == b"\xff\xd8\xff\xd9"


@pytest.mark.asyncio
async def test_upload_image_rejects_unsupported_content_type(async_client: AsyncClient, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    headers = await _signup(async_client, "upload-org-badtype")

    res = await async_client.post(
        "/api/v1/campaigns/upload-image",
        headers=headers,
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_upload_image_rejects_file_over_8mb(async_client: AsyncClient, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    headers = await _signup(async_client, "upload-org-toobig")

    oversized = b"0" * (8 * 1024 * 1024 + 1)
    res = await async_client.post(
        "/api/v1/campaigns/upload-image",
        headers=headers,
        files={"file": ("photo.jpg", oversized, "image/jpeg")},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_get_or_upload_image_reads_local_file_for_image_path(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))

    org_dir = tmp_path / "org_1"
    org_dir.mkdir()
    (org_dir / "photo.jpg").write_bytes(b"fake-jpeg-bytes")

    class _NoNetworkLimiter:
        async def call_api_multipart(self, endpoint, data, file_bytes, filename, access_token=""):
            assert file_bytes == b"fake-jpeg-bytes"
            assert filename == "photo.jpg"
            return {"images": {filename: {"hash": "hash_from_local_file"}}}

    repo = MetaAssetRepository(_NoNetworkLimiter())
    result = await repo.get_or_upload_image(
        ad_account_external_id="act_1", access_token="tok",
        image_path="org_1/photo.jpg",
    )
    assert result == "hash_from_local_file"


@pytest.mark.asyncio
async def test_get_or_upload_image_rejects_path_traversal(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    (tmp_path / "media").mkdir()
    (tmp_path / "secret.txt").write_bytes(b"outside media root")

    class _NoNetworkLimiter:
        async def call_api_multipart(self, *a, **kw):
            raise AssertionError("no debería llegar a llamar a Meta")

    repo = MetaAssetRepository(_NoNetworkLimiter())
    with pytest.raises(ValueError):
        await repo.get_or_upload_image(
            ad_account_external_id="act_1", access_token="tok",
            image_path="../secret.txt",
        )
