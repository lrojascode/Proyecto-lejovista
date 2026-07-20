from pydantic import BaseModel, Field
from typing import Literal, Optional

# Modo de presupuesto del AdSet. LIFETIME = monto total repartido en `days`
# (`lifetime_budget`, el comportamiento histórico y el default); DAILY = monto
# por día (`daily_budget`), que es como trabaja la mayoría de anunciantes.
BudgetType = Literal["LIFETIME", "DAILY"]


class CampaignRiskPreviewRequest(BaseModel):
    """Valores actuales del formulario para calcular el nivel de riesgo ANTES de
    confirmar la creación (Ajuste 1)."""
    ad_account_id: str
    budget: float = Field(..., gt=0)
    copy_text: str = ""


class CampaignPreflightRequest(BaseModel):
    """Valida contra Meta (`validate_only`) los payloads que enviaría la Saga,
    sin crear nada. Mismos campos que la creación, salvo los que solo importan
    al ejecutar de verdad."""
    ad_account_id: str
    page_id: str
    product_name: str
    budget: float = Field(..., gt=0)
    budget_type: BudgetType = "LIFETIME"
    days: int = Field(default=7, gt=0)
    copy_text: str = ""
    whatsapp_number: Optional[str] = None
    image_hash: Optional[str] = None
    headline: Optional[str] = None
    description: Optional[str] = None


class CampaignCreateRequest(BaseModel):
    ad_account_id: str
    page_id: str
    product_name: str = Field(..., description="Nombre del producto o servicio (e.g. Cable GPT)")
    budget: float = Field(..., gt=0, description="Presupuesto en la moneda local: total o diario según budget_type")
    budget_type: BudgetType = Field(
        default="LIFETIME",
        description="LIFETIME = `budget` es el total de la campaña; DAILY = es el monto por día",
    )
    days: int = Field(..., gt=0, description="Duración en días de la campaña")
    whatsapp_number: str = Field(..., description="Número de WhatsApp sin + ni espacios")
    image_url: Optional[str] = None
    image_hash: Optional[str] = None
    # Ruta devuelta por POST /campaigns/upload-image (foto subida a la app,
    # ver Ajuste "subir imagen en vez de URL").
    image_path: Optional[str] = None
    # Video/UGC (playbook §5 Paso 4: "el video suele rendir mejor que la imagen
    # estática"). Si se provee video_id, se ignoran image_url/image_hash.
    video_id: Optional[str] = Field(default=None, description="ID del video ya subido a Meta (/advideos)")
    thumbnail_url: Optional[str] = Field(default=None, description="URL de la miniatura, requerida junto a video_id")
    # Ruta devuelta por POST /campaigns/upload-video: la Saga lo sube a Meta,
    # espera el procesado y resuelve la miniatura sola (no hace falta darla).
    video_path: Optional[str] = Field(default=None, description="Ruta del video subido a la app")
    copy_text: str = Field(..., description="Texto completo del anuncio, incluyendo hook, bullets y CTA")
    # 3.4: variante de headline/description elegida por el usuario en la UI
    # (de las 2 que genera el copy v2). Opcionales — copy_text sigue siendo
    # el único campo obligatorio del anuncio. Se validan contra los límites
    # de Meta en CampaignValidator si vienen presentes.
    headline: Optional[str] = Field(default=None, description="Headline elegido (máx. 40 caracteres, límite de Meta)")
    description: Optional[str] = Field(default=None, description="Description elegida (máx. 30 caracteres, límite de Meta)")

    def total_budget(self) -> float:
        """Presupuesto TOTAL de la campaña, sea cual sea el modo.

        Es la cifra comparable entre campañas y la única que entienden el
        cálculo de riesgo y el ciclo de aprendizaje (que deriva el costo
        esperado por conversación de un total). Con DAILY se multiplica por
        los días de duración.
        """
        if self.budget_type == "DAILY":
            return round(self.budget * self.days, 2)
        return self.budget

    class Config:
        json_schema_extra = {
            "example": {
                "ad_account_id": "act_123456789",
                "page_id": "111222333",
                "product_name": "Cable Vulcano",
                "budget": 200.0,
                "days": 5,
                "whatsapp_number": "51999888777",
                "image_hash": "abcdef123456",
                "copy_text": "¡Mejora tu conectividad!..."
            }
        }
