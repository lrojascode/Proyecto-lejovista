import re
import logging
import unicodedata
from typing import Optional

from fastapi import HTTPException
from app.config import settings
from app.models.ad_account import AdAccount
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter
from app.services.ai.llm import META_HEADLINE_MAX_CHARS, META_DESCRIPTION_MAX_CHARS

logger = logging.getLogger(__name__)

# Letras, números, espacios y guiones básicos — sin tildes, ñ ni emojis.
# Constante importable para que el chat de creación (CH-1) pueda normalizar
# `product_name` ANTES de llegar acá, en vez de duplicar la regex.
PRODUCT_NAME_PATTERN = r"^[A-Za-z0-9 _-]+$"

_DISALLOWED_CHARS_RE = re.compile(r"[^A-Za-z0-9 _-]")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Transliteración ASCII (tildes, ñ, emojis fuera). Sin esto, cualquier
    frase en español real rompe `PRODUCT_NAME_PATTERN`."""
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize_product_name(raw: Optional[str]) -> str:
    """Adapta un `product_name` escrito por una persona a lo que exige Meta,
    sin cambiar lo que dijo: "diseño web a medida" → "diseno web a medida".

    Es deliberadamente MÍNIMA —solo transliterar y sacar lo que la regex no
    admite— a diferencia de `campaign_chat.slots.sanitize_product_name`, que
    además tira palabras de relleno y capitaliza porque ahí el nombre se está
    DERIVANDO de una frase hablada. Acá la persona escribió el nombre en un
    campo a propósito: reordenarle las palabras sería una sorpresa, dejarla
    contra un error que no puede resolver sola es peor.
    """
    cleaned = _DISALLOWED_CHARS_RE.sub(" ", strip_accents(raw or ""))
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


class CampaignValidator:
    """
    Ejecuta validaciones síncronas antes de encolar la creación de una campaña.
    Verifica reglas de negocio y hace un request de validación (estado de cuenta) a Meta.

    Antes recibía `tenant_id` + `MetaApiClient`, que resolvía el token vía
    `MetaCredentialService.get_valid_token(organization_id)` — una consulta que
    solo consideraba la ORGANIZACIÓN, no la cuenta publicitaria específica.
    Con multi-cuenta (2+ AdAccount por organización) eso rompía con
    `MultipleResultsFound` en cuanto la org tenía más de una credencial activa.
    Ahora recibe el `AdAccount` ya resuelto y usa `MetaAPIRateLimiter` (el mismo
    stack real usado por el resto de la app), sin ambigüedad posible.
    """
    def __init__(self, ad_account: AdAccount, limiter: MetaAPIRateLimiter | None = None, db=None):
        self.ad_account = ad_account
        self.limiter = limiter or MetaAPIRateLimiter(ad_account_id=ad_account.id, external_account_id=ad_account.external_account_id)
        # Opcional: si se provee, permite marcar la cuenta EXPIRED cuando Meta
        # rechaza el token (code 190), igual que el sync (_maybe_mark_expired) —
        # así el badge "Token expirado" aparece en Configuración sin esperar al
        # siguiente sync nocturno.
        self.db = db

    async def validate(self, request: CampaignCreateRequest) -> None:
        """
        Valida síncronamente el request y la cuenta.
        Lanza HTTPException si algo no cumple.
        """
        self._validate_business_rules(request)
        await self._verify_meta_account_status()

    def _validate_business_rules(self, request: CampaignCreateRequest):
        # 1. Sin imagen, hash, ni video (playbook §5 Paso 4: soporte de video/UGC).
        # `image_path` es una imagen local ya subida a nuestro disco (vía
        # POST /campaigns/upload-image): la Saga la sube a Meta en task_upload_image
        # para derivar el image_hash, así que es una forma válida de proveer imagen.
        # `video_path` es un video local subido vía POST /campaigns/upload-video:
        # la Saga lo sube a Meta y resuelve la miniatura sola.
        if (not request.image_url and not request.image_hash
                and not request.video_id and not request.image_path
                and not request.video_path):
            raise HTTPException(
                status_code=400,
                detail="Debes subir una imagen o un video, o proveer image_url, image_hash o video_id.",
            )
        # Solo se exige miniatura con un video_id YA subido a Meta: para
        # `video_path` la resuelve la Saga (`/{video_id}/thumbnails`).
        if request.video_id and not request.thumbnail_url:
            raise HTTPException(status_code=400, detail="video_id requires thumbnail_url.")

        # 2. Sanitización de nombre de producto/zona (no tildes ni emojis)
        # Permite letras, números, espacios y guiones bajos básicos
        if not re.match(PRODUCT_NAME_PATTERN, request.product_name):
            raise HTTPException(status_code=400, detail="product_name contains invalid characters (no accents or emojis allowed).")

        # 3. Límites de caracteres de Meta para el Ad Creative (3.4). Solo se
        # validan si vienen presentes — son opcionales (variante elegida en la
        # UI de las 2 que genera el copy v2).
        if request.headline and len(request.headline) > META_HEADLINE_MAX_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"headline excede el límite de Meta de {META_HEADLINE_MAX_CHARS} caracteres "
                       f"({len(request.headline)}).",
            )
        if request.description and len(request.description) > META_DESCRIPTION_MAX_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"description excede el límite de Meta de {META_DESCRIPTION_MAX_CHARS} caracteres "
                       f"({len(request.description)}).",
            )

    async def _verify_meta_account_status(self):
        """
        Paso 1 del documento: GET /act_[ID]?fields=amount_spent,spend_cap,account_status

        `external_account_id` ya incluye el prefijo `act_` (confirmado contra
        Meta real en sync.py/creation_tasks.py) — no hay que añadirlo de nuevo.
        """
        if settings.MOCK_SERVICES:
            # Mismo criterio que el resto de la app (sync mock, modifier):
            # en modo simulado no hay nada real que validar contra Meta.
            return

        try:
            token = self.ad_account.get_access_token()
            response = await self.limiter.call_api(
                self.ad_account.external_account_id,
                {"fields": "amount_spent,spend_cap,account_status"},
                access_token=token,
            )

            if not isinstance(response, dict) or "error" in response or "ERROR" in response:
                logger.error(f"Meta respondió con error al validar la cuenta {self.ad_account.id}: {response}")
                # Token expirado/ inválido (OAuthException code 190): marca la
                # cuenta EXPIRED y da un mensaje accionable — el genérico "Meta
                # rechazó la validación" no le dice al usuario que debe reconectar.
                err = response.get("error", {}) if isinstance(response, dict) else {}
                if err.get("code") == 190:
                    await self._mark_account_expired()
                    raise HTTPException(
                        status_code=401,
                        detail="El token de la cuenta expiró. Reconéctala en Configuración con un token nuevo.",
                    )
                raise HTTPException(status_code=502, detail="Meta rechazó la validación de la cuenta de anuncios.")

            # account_status = 1 es ACTIVO
            status = response.get("account_status")
            if status != 1:
                logger.error(f"Account {self.ad_account.id} has invalid status {status}")
                raise HTTPException(status_code=400, detail=f"Cannot create campaign. Ad Account status is {status} (expected 1).")

            # verificar spend_cap
            spend_cap = float(response.get("spend_cap", 0))
            amount_spent = float(response.get("amount_spent", 0))

            if spend_cap > 0 and amount_spent >= spend_cap:
                logger.error(f"Account {self.ad_account.id} has reached its spend cap ({amount_spent}/{spend_cap})")
                raise HTTPException(status_code=400, detail="Spend cap reached. Please increase your limit in Meta Ads Manager.")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error validating meta account: {e}")
            raise HTTPException(status_code=500, detail="Failed to validate Ad Account against Meta API.")

    async def _mark_account_expired(self):
        """Marca la cuenta (y su credencial) como EXPIRED tras un code 190, para
        que Configuración muestre 'Token expirado' de inmediato. No-op si no se
        pasó una sesión de BD al validador."""
        if self.db is None:
            return
        try:
            self.ad_account.status = "EXPIRED"
            if self.ad_account.meta_credential:
                self.ad_account.meta_credential.status = "EXPIRED"
            self.db.add(self.ad_account)
            await self.db.commit()
        except Exception:
            logger.exception("No se pudo marcar la cuenta %s como EXPIRED", self.ad_account.id)
