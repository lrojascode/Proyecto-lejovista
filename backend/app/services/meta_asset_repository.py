import logging
import httpx
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

logger = logging.getLogger(__name__)

class MetaAssetRepository:
    """
    Maneja la subida o reutilización de Assets (Imágenes/Videos) en Meta Ads.
    """
    def __init__(self, limiter: MetaAPIRateLimiter):
        self.limiter = limiter

    async def get_or_upload_image(
        self,
        ad_account_external_id: str,
        access_token: str,
        image_url: str | None = None,
        image_hash: str | None = None,
    ) -> str:
        """
        Retorna un image_hash válido.
        Si se pasa un image_hash, lo usa directamente (no se re-sube).
        Si se pasa una image_url, descarga la imagen y la sube a Meta vía
        POST multipart a `/{act}/adimages` (real si ENABLE_META_WRITEBACK,
        simulado si no — ver MetaAPIRateLimiter.call_api_multipart).
        """
        if image_hash:
            logger.info(f"Reusing existing image_hash: {image_hash}")
            return image_hash

        if not image_url:
            raise ValueError("Must provide either image_hash or image_url")

        logger.info(f"Descargando imagen desde {image_url} para subir a Meta")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            file_bytes = response.content

        filename = image_url.rsplit("/", 1)[-1] or "campaign_image.jpg"
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
