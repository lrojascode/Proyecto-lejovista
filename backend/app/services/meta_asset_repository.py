import asyncio
import logging
import os
import httpx
from app.config import settings
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

logger = logging.getLogger(__name__)

# Meta procesa el video en asíncrono: recién subido no sirve para crear un
# anuncio (Meta responde que el video no está listo). Se sondea su estado.
_VIDEO_READY_TIMEOUT_S = 240
_VIDEO_POLL_INTERVAL_S = 5
# Subida simple (no chunked): 100MB cubre de sobra un creativo publicitario.
_VIDEO_UPLOAD_TIMEOUT_S = 600


class MetaAssetRepository:
    """
    Maneja la subida o reutilización de Assets (Imágenes/Videos) en Meta Ads.
    """
    def __init__(self, limiter: MetaAPIRateLimiter):
        self.limiter = limiter

    def _read_from_media_root(self, rel_path: str) -> tuple[bytes, str]:
        """Lee un archivo subido a la app validando que no se escape de
        MEDIA_ROOT (`rel_path` viene del cliente)."""
        full_path = os.path.realpath(os.path.join(settings.MEDIA_ROOT, rel_path))
        media_root = os.path.realpath(settings.MEDIA_ROOT)
        if not full_path.startswith(media_root + os.sep):
            raise ValueError("Ruta de archivo inválida.")
        with open(full_path, "rb") as f:
            return f.read(), os.path.basename(full_path)

    async def get_or_upload_image(
        self,
        ad_account_external_id: str,
        access_token: str,
        image_url: str | None = None,
        image_hash: str | None = None,
        image_path: str | None = None,
    ) -> str:
        """
        Retorna un image_hash válido.
        Si se pasa un image_hash, lo usa directamente (no se re-sube).
        Si se pasa un image_path (foto subida a la app, ver POST
        /campaigns/upload-image), se lee del disco compartido (MEDIA_ROOT) —
        sin round-trip HTTP, `backend` y este worker comparten el mismo volumen.
        Si se pasa una image_url, descarga la imagen y la sube a Meta vía
        POST multipart a `/{act}/adimages` (real si ENABLE_META_WRITEBACK,
        simulado si no — ver MetaAPIRateLimiter.call_api_multipart).
        """
        if image_hash:
            logger.info(f"Reusing existing image_hash: {image_hash}")
            return image_hash

        if image_path:
            logger.info(f"Leyendo imagen subida a la app: {image_path}")
            file_bytes, filename = self._read_from_media_root(image_path)
        elif image_url:
            logger.info(f"Descargando imagen desde {image_url} para subir a Meta")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(image_url)
                response.raise_for_status()
                file_bytes = response.content
            filename = image_url.rsplit("/", 1)[-1] or "campaign_image.jpg"
        else:
            raise ValueError("Must provide either image_hash, image_path or image_url")

        endpoint = f"{ad_account_external_id}/adimages"

        result = await self.limiter.call_api_multipart(
            endpoint, {}, file_bytes, filename, access_token=access_token
        )

        if not isinstance(result, dict) or "error" in result or "ERROR" in result:
            raise Exception(f"Meta API falló al subir la imagen: {result}")

        images = result.get("images", {})
        image_data = images.get(filename) or next(iter(images.values()), None)
        if not image_data or "hash" not in image_data:
            raise Exception(f"Respuesta de Meta sin hash de imagen: {result}")

        return image_data["hash"]

    async def get_or_upload_video(
        self,
        ad_account_external_id: str,
        access_token: str,
        video_path: str,
        thumbnail_url: str | None = None,
    ) -> tuple[str, str | None]:
        """Sube un video a `/{act}/advideos` y devuelve `(video_id, thumbnail_url)`.

        Tres pasos, y los dos últimos existen porque Meta NO deja crear el
        anuncio con un video recién subido:
          1. POST multipart con el binario en el campo `source`, contra
             `graph-video.facebook.com`.
          2. Sondeo de `status` hasta que el procesado termina; crear el
             creativo antes falla con un error poco descriptivo.
          3. Miniatura: Meta genera varias y marca una `is_preferred`. Es
             obligatoria para `video_data`, así que si el usuario no dio una
             se toma esa.

        Subida simple (no chunked): el tope de 100MB lo impone el endpoint de
        la app, y por debajo de eso el protocolo por partes no aporta.
        """
        file_bytes, filename = self._read_from_media_root(video_path)
        logger.info(f"Subiendo video {filename} ({len(file_bytes) / 1024 / 1024:.1f}MB) a Meta")

        result = await self.limiter.call_api_multipart(
            f"{ad_account_external_id}/advideos", {}, file_bytes, filename,
            access_token=access_token,
            timeout=_VIDEO_UPLOAD_TIMEOUT_S,
            video_host=True,
            field_name="source",
            mock_response={"id": f"mock_video_{filename}"},
        )
        if not isinstance(result, dict) or "error" in result or "ERROR" in result:
            raise Exception(f"Meta API falló al subir el video: {result}")

        video_id = result.get("id") or result.get("video_id")
        if not video_id:
            raise Exception(f"Respuesta de Meta sin id de video: {result}")

        if result.get("mock"):
            return video_id, thumbnail_url or "https://example.com/mock_thumbnail.jpg"

        await self._wait_until_video_ready(video_id, access_token)

        if not thumbnail_url:
            thumbnail_url = await self._preferred_thumbnail(video_id, access_token)
        return video_id, thumbnail_url

    async def _wait_until_video_ready(self, video_id: str, access_token: str) -> None:
        """Sondea `status` hasta que Meta termina de procesar el video."""
        deadline = asyncio.get_event_loop().time() + _VIDEO_READY_TIMEOUT_S
        last_status = None

        while asyncio.get_event_loop().time() < deadline:
            res = await self.limiter.call_api(
                str(video_id), {"fields": "status"}, access_token=access_token
            )
            status = (res or {}).get("status") or {}
            last_status = status.get("video_status") or status.get("processing_phase", {}).get("status")

            if last_status == "ready":
                return
            if last_status == "error":
                raise Exception(f"Meta no pudo procesar el video {video_id}: {status}")

            await asyncio.sleep(_VIDEO_POLL_INTERVAL_S)

        # No se lanza excepción: Meta a veces tarda más de la cuenta y el
        # creativo igual se crea. Si no está listo, ese paso fallará con el
        # error real de Meta, que es más informativo que un timeout nuestro.
        logger.warning(
            "El video %s sigue en estado '%s' tras %ss; se continúa igualmente.",
            video_id, last_status, _VIDEO_READY_TIMEOUT_S,
        )

    async def _preferred_thumbnail(self, video_id: str, access_token: str) -> str | None:
        """Miniatura que Meta marca como preferida para este video."""
        res = await self.limiter.call_api(
            f"{video_id}/thumbnails", {"fields": "uri,is_preferred"}, access_token=access_token
        )
        thumbs = (res or {}).get("data") or []
        if not thumbs:
            logger.warning("Meta no devolvió miniaturas para el video %s", video_id)
            return None
        preferred = next((t for t in thumbs if t.get("is_preferred")), thumbs[0])
        return preferred.get("uri")
