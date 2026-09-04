"""Subida de video MP4 (F7).

`CreativeBuilder` ya sabía armar `video_data`, pero conseguir el `video_id` y la
miniatura había que hacerlo a mano fuera de la app. Lo que se agrega aquí es el
camino completo: endpoint de subida → `/advideos` → esperar el procesado →
miniatura preferida.
"""
import io

import pytest

from app.services.meta_asset_repository import MetaAssetRepository


class _FakeLimiter:
    """Registra las llamadas para poder afirmar sobre el protocolo, y devuelve
    respuestas encoladas."""

    def __init__(self, multipart_result=None, api_results=None):
        self.multipart_result = multipart_result or {"id": "vid_1"}
        self.api_results = list(api_results or [])
        self.multipart_calls = []
        self.api_calls = []

    async def call_api_multipart(self, endpoint, data, file_bytes, filename, **kwargs):
        self.multipart_calls.append({"endpoint": endpoint, "filename": filename,
                                     "size": len(file_bytes), **kwargs})
        return self.multipart_result

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        self.api_calls.append(endpoint)
        return self.api_results.pop(0) if self.api_results else {}


@pytest.fixture
def media_video(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    org_dir = tmp_path / "org-1"
    org_dir.mkdir()
    (org_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 500)
    return "org-1/clip.mp4"


@pytest.mark.asyncio
async def test_video_upload_uses_video_host_and_source_field(media_video):
    """Meta sirve la subida por graph-video y espera el binario en `source`,
    no bajo el nombre del archivo como hace /adimages."""
    limiter = _FakeLimiter(
        multipart_result={"id": "vid_1"},
        api_results=[
            {"status": {"video_status": "ready"}},
            {"data": [{"uri": "http://t/1.jpg", "is_preferred": False},
                      {"uri": "http://t/2.jpg", "is_preferred": True}]},
        ],
    )

    video_id, thumb = await MetaAssetRepository(limiter).get_or_upload_video(
        "act_1", "tok", media_video,
    )

    call = limiter.multipart_calls[0]
    assert call["endpoint"] == "act_1/advideos"
    assert call["video_host"] is True
    assert call["field_name"] == "source"
    assert call["timeout"] >= 600  # 30s no alcanza para 100MB

    assert video_id == "vid_1"
    # De las miniaturas que genera Meta se toma la marcada como preferida.
    assert thumb == "http://t/2.jpg"


@pytest.mark.asyncio
async def test_waits_for_processing_before_returning(media_video):
    """Crear el anuncio con un video aún en proceso falla en Meta con un error
    poco descriptivo, así que se sondea hasta que está listo."""
    limiter = _FakeLimiter(api_results=[
        {"status": {"video_status": "processing"}},
        {"status": {"video_status": "ready"}},
        {"data": [{"uri": "http://t/1.jpg", "is_preferred": True}]},
    ])

    await MetaAssetRepository(limiter).get_or_upload_video("act_1", "tok", media_video)

    # Dos sondeos de estado + la consulta de miniaturas.
    assert limiter.api_calls == ["vid_1", "vid_1", "vid_1/thumbnails"]


@pytest.mark.asyncio
async def test_processing_error_raises(media_video):
    limiter = _FakeLimiter(api_results=[{"status": {"video_status": "error"}}])

    with pytest.raises(Exception, match="no pudo procesar el video"):
        await MetaAssetRepository(limiter).get_or_upload_video("act_1", "tok", media_video)


@pytest.mark.asyncio
async def test_explicit_thumbnail_skips_the_lookup(media_video):
    limiter = _FakeLimiter(api_results=[{"status": {"video_status": "ready"}}])

    _, thumb = await MetaAssetRepository(limiter).get_or_upload_video(
        "act_1", "tok", media_video, thumbnail_url="http://mia.jpg",
    )

    assert thumb == "http://mia.jpg"
    assert "vid_1/thumbnails" not in limiter.api_calls


@pytest.mark.asyncio
async def test_meta_error_is_raised(media_video):
    limiter = _FakeLimiter(multipart_result={"error": {"message": "File too large"}})

    with pytest.raises(Exception, match="falló al subir el video"):
        await MetaAssetRepository(limiter).get_or_upload_video("act_1", "tok", media_video)


@pytest.mark.asyncio
async def test_path_traversal_is_rejected(tmp_path, monkeypatch):
    """`video_path` llega del cliente y termina leyendo del disco."""
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    (tmp_path / "media").mkdir()
    (tmp_path / "secreto.mp4").write_bytes(b"secreto")

    with pytest.raises(ValueError, match="Ruta de archivo inválida"):
        await MetaAssetRepository(_FakeLimiter()).get_or_upload_video(
            "act_1", "tok", "../secreto.mp4",
        )


# --- Endpoint de subida ---

async def _signup(async_client, slug):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@test.com", "password": "Password123!",
        "full_name": "Video Tester", "organization_slug": slug,
        "organization_name": "Video Org",
    })
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.mark.asyncio
async def test_upload_video_rejects_non_video(async_client, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    headers = await _signup(async_client, "video-a")

    res = await async_client.post(
        "/api/v1/campaigns/upload-video", headers=headers,
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert res.status_code == 400
    # El PDF se rechaza por su CONTENIDO, no por el tipo que declara.
    assert res.json()["detail"]["code"] == "MEDIA_UNSUPPORTED_TYPE"
    assert "MP4" in res.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_upload_video_returns_scoped_path(async_client, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    headers = await _signup(async_client, "video-b")

    # Un MP4 REAL: desde la spec 07 el archivo se abre de verdad, así que mil
    # bytes de ceros con nombre de video ya no pasan.
    from tests.fixtures import mp4_real

    res = await async_client.post(
        "/api/v1/campaigns/upload-video", headers=headers,
        files={"file": ("clip.mp4", mp4_real(), "video/mp4")},
    )
    assert res.status_code == 200, res.text
    video_path = res.json()["video_path"]
    # Scopeado por organización, igual que las imágenes.
    assert video_path.endswith(".mp4") and "/" in video_path
    assert (tmp_path / video_path).exists()


@pytest.mark.asyncio
async def test_upload_video_rejects_oversized(async_client, tmp_path, monkeypatch):
    """El límite se aplica DURANTE la transferencia (spec 07), no al final."""
    from app.config import settings
    from app.services import media_service
    from tests.fixtures import mp4_real

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(media_service, "MAX_VIDEO_BYTES", 512)
    headers = await _signup(async_client, "video-c")

    res = await async_client.post(
        "/api/v1/campaigns/upload-video", headers=headers,
        files={"file": ("big.mp4", mp4_real(), "video/mp4")},
    )
    assert res.status_code == 413
    assert res.json()["detail"]["code"] == "MEDIA_TOO_LARGE"
