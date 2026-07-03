from pydantic import BaseModel, Field
from typing import Optional

class CampaignCreateRequest(BaseModel):
    ad_account_id: str
    page_id: str
    product_name: str = Field(..., description="Nombre del producto o servicio (e.g. Cable GPT)")
    zone: str = Field(..., description="Zona a la que apunta (e.g. Cercado)")
    budget: float = Field(..., gt=0, description="Presupuesto total (lifetime budget) en la moneda local")
    days: int = Field(..., gt=0, description="Duración en días de la campaña")
    whatsapp_number: str = Field(..., description="Número de WhatsApp sin + ni espacios")
    image_url: Optional[str] = None
    image_hash: Optional[str] = None
    copy_text: str = Field(..., description="Texto completo del anuncio, incluyendo hook, bullets y CTA")

    class Config:
        json_schema_extra = {
            "example": {
                "ad_account_id": "act_123456789",
                "page_id": "111222333",
                "product_name": "Cable Vulcano",
                "zone": "Surquillo",
                "budget": 200.0,
                "days": 5,
                "whatsapp_number": "51999888777",
                "image_hash": "abcdef123456",
                "copy_text": "¡Mejora tu conectividad!..."
            }
        }
