import re
from fastapi import HTTPException
from typing import Dict, Any
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.meta_api_client import MetaApiClient
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES
import logging

logger = logging.getLogger(__name__)

class CampaignValidator:
    """
    Ejecuta validaciones síncronas antes de encolar la creación de una campaña.
    Verifica reglas de negocio y hace un request de validación (estado de cuenta) a Meta.
    """
    def __init__(self, tenant_id: str, meta_client: MetaApiClient):
        self.tenant_id = tenant_id
        self.meta_client = meta_client

    async def validate(self, request: CampaignCreateRequest) -> None:
        """
        Valida síncronamente el request y la cuenta.
        Lanza HTTPException si algo no cumple.
        """
        self._validate_business_rules(request)
        await self._verify_meta_account_status(request.ad_account_id)

    def _validate_business_rules(self, request: CampaignCreateRequest):
        # 1. Sin imagen, hash, ni video (playbook §5 Paso 4: soporte de video/UGC)
        if not request.image_url and not request.image_hash and not request.video_id:
            raise HTTPException(status_code=400, detail="Must provide image_url, image_hash, or video_id.")
        if request.video_id and not request.thumbnail_url:
            raise HTTPException(status_code=400, detail="video_id requires thumbnail_url.")
        
        # 2. Sanitización de nombre de producto/zona (no tildes ni emojis)
        # Permite letras, números, espacios y guiones bajos básicos
        if not re.match(r"^[A-Za-z0-9 _-]+$", request.product_name):
            raise HTTPException(status_code=400, detail="product_name contains invalid characters (no accents or emojis allowed).")
            
        if not re.match(r"^[A-Za-z0-9 _-]+$", request.zone):
            raise HTTPException(status_code=400, detail="zone contains invalid characters (no accents or emojis allowed).")

    async def _verify_meta_account_status(self, ad_account_id: str):
        """
        Paso 1 del documento: GET /act_[ID]?fields=amount_spent,spend_cap,account_status
        """
        try:
            # Asegurar prefijo act_
            endpoint_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
            response = await self.meta_client.get(
                endpoint=f"/{endpoint_id}",
                ad_account_id=ad_account_id,
                params={"fields": "amount_spent,spend_cap,account_status"}
            )
            
            # account_status = 1 es ACTIVO
            status = response.get("account_status")
            if status != 1:
                logger.error(f"Account {ad_account_id} has invalid status {status}")
                raise HTTPException(status_code=400, detail=f"Cannot create campaign. Ad Account status is {status} (expected 1).")
            
            # verificar spend_cap
            spend_cap = float(response.get("spend_cap", 0))
            amount_spent = float(response.get("amount_spent", 0))
            
            if spend_cap > 0 and amount_spent >= spend_cap:
                logger.error(f"Account {ad_account_id} has reached its spend cap ({amount_spent}/{spend_cap})")
                raise HTTPException(status_code=400, detail="Spend cap reached. Please increase your limit in Meta Ads Manager.")
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error validating meta account: {e}")
            raise HTTPException(status_code=500, detail="Failed to validate Ad Account against Meta API.")
