from pydantic import BaseModel, Field, model_validator
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


# --- Chat de creación de campañas (CH-1) ---

class ChatSessionCreateRequest(BaseModel):
    """`ad_account_id` es opcional: si falta, se usa la cuenta activa
    (header `X-Ad-Account-Id`, igual que el resto de la app)."""
    ad_account_id: Optional[str] = None


class ChatMessageRequest(BaseModel):
    """Turno de texto libre o de un archivo ya subido (`POST
    /campaigns/upload-image|upload-video` corre ANTES; acá solo viaja la ruta
    resultante). `client_turn_id` deja repetir un turno sin duplicar efectos
    ante un reintento de red."""
    client_turn_id: str = Field(..., max_length=64)
    text: Optional[str] = Field(None, max_length=2000)
    image_path: Optional[str] = None
    video_path: Optional[str] = None

    @model_validator(mode="after")
    def _requires_some_payload(self):
        if not self.text and not self.image_path and not self.video_path:
            raise ValueError("El mensaje necesita texto o un archivo.")
        return self


class ChatDecisionRequest(BaseModel):
    """La compuerta estilo Claude Code: aceptar/rechazar/cambiar una tarjeta
    de revisión o de riesgo. `card_id` referencia la tarjeta a la que
    responde, para no poder contestar una vieja tras un reload."""
    client_turn_id: str = Field(..., max_length=64)
    card_id: str
    decision: Literal["accept", "reject", "change"]
    text: Optional[str] = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _change_requires_text(self):
        if self.decision == "change" and not self.text:
            raise ValueError("decision='change' requiere 'text' con lo que hay que cambiar.")
        return self
