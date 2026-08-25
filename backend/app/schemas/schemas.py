from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from datetime import datetime, date
from typing import List, Dict, Any, Literal, Optional

# --- AUTH SCHEMAS ---
class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    organization_slug: str  # Funda una organización nueva (S-1: no une a una existente)
    organization_name: str

class PasswordChangeRequest(BaseModel):
    """Cambio con sesión activa. Exige la actual: una sesión robada no debe
    poder cambiar la contraseña y dejar al dueño afuera."""
    current_password: str
    new_password: str = Field(min_length=8)


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    email: Optional[EmailStr] = None


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    organization_id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class TokenResponse(BaseModel):
    # (S-2) El refresh token viaja SOLO como cookie httpOnly — nunca en el
    # cuerpo JSON, para que no quede alcanzable desde JavaScript (localStorage)
    # ante una vulnerabilidad XSS.
    access_token: str
    token_type: str
    user: UserResponse

# --- ORGANIZATION SCHEMAS ---
class OrganizationResponse(BaseModel):
    id: str
    slug: str
    name: str
    display_name: Optional[str] = None
    logo_url: Optional[str] = None
    accent_color: Optional[str] = None
    ai_model: str
    llm_provider: Optional[str] = None
    has_llm_api_key: bool = False
    llm_api_key_preview: Optional[str] = None
    max_budget: float
    status: str
    industry: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    # El acento se inyecta como variable CSS en el layout del cliente. Un valor
    # libre ahi seria una via de inyeccion de CSS, asi que se acota a un hex —
    # que ademas es lo unico que el selector de color de Settings produce.
    accent_color: Optional[str] = Field(default=None, pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
    ai_model: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    max_budget: Optional[float] = None
    industry: Optional[str] = None

class CampaignRulesConfigUpdate(BaseModel):
    overrides: Dict[str, Any]

class AIThresholdsConfigUpdate(BaseModel):
    overrides: Dict[str, Any]

class TenantBrandResponse(BaseModel):
    name: str
    display_name: Optional[str] = None
    logo_url: Optional[str] = None
    accent_color: Optional[str] = None

class LLMKeyTestRequest(BaseModel):
    provider: str  # openai | gemini_direct | openrouter
    api_key: str
    model: Optional[str] = None

class LLMKeyTestResponse(BaseModel):
    valid: bool
    message: str
    provider: str

# --- AD ACCOUNT SCHEMAS ---
# App de Meta PROPIA de la cuenta. Se deja vacío salvo que el token venga de la
# app del cliente y no de la de la agencia — el caso de un System User que vive
# en el Business Manager del cliente y solo puede emitir tokens con la app de
# ese negocio. Sin esto, `debug_token` falla y la revisión diaria marca la
# cuenta EXPIRED sobre un token sano.
class _AppPropiaMixin(BaseModel):
    app_id: Optional[str] = Field(
        None, description="App ID de Meta que emitió el token. Vacío = la app de la agencia."
    )
    app_secret: Optional[str] = Field(
        None, description="App Secret de esa app. Se guarda cifrado y nunca se devuelve."
    )


class AdAccountCreate(_AppPropiaMixin):
    platform: str # META, GOOGLE
    external_account_id: str
    name: str
    access_token: str
    refresh_token: Optional[str] = None

class AdAccountReconnect(_AppPropiaMixin):
    access_token: str = Field(..., min_length=10, description="Nuevo Access Token de Meta Graph API")

class AdAccountResponse(BaseModel):
    id: str
    platform: str
    external_account_id: str
    name: str
    status: str
    default_page_id: Optional[str] = None
    spend_cap: float
    amount_spent: float
    last_sync_error_code: Optional[int] = None
    last_sync_error_detail: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None
    token_expires_in_days: Optional[int] = None
    token_warning: Optional[bool] = None
    # Solo el ID: el secret se guarda cifrado y no sale nunca de la base.
    app_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemberRoleUpdate(BaseModel):
    role: Literal["ADMIN", "OPERATOR", "VIEWER"]


class MemberAdAccountsUpdate(BaseModel):
    # Reemplaza el conjunto completo, no suma: la pantalla manda el estado final
    # de los checkboxes. Una lista vacía es válida (deja al miembro sin acceso),
    # salvo que sea el propio ADMIN que la está mandando.
    ad_account_ids: List[str] = []


class AdAccountDefaultPageUpdate(BaseModel):
    # None limpia la página predeterminada (vuelve al comportamiento anterior:
    # el executor solo resuelve si hay exactamente 1 página con WhatsApp).
    page_id: Optional[str] = Field(None, description="MetaPage.id (interno), o null para limpiar")

# --- META PAGE SCHEMA (Fase 4: descubrimiento de página + WhatsApp) ---
class MetaPageResponse(BaseModel):
    id: str
    external_page_id: str
    name: str
    instagram_actor_id: Optional[str] = None
    whatsapp_number: Optional[str] = None
    status: str

    model_config = ConfigDict(from_attributes=True)

class MetaPageWhatsAppUpdate(BaseModel):
    whatsapp_number: str = Field(..., description="Número de WhatsApp sin + ni espacios")


# --- Catálogo de números de WhatsApp de la organización (F1) ---

class WhatsAppNumberResponse(BaseModel):
    id: str
    display_number: str
    # META = descubierto en Meta (lo reescribe el sync, no editable);
    # MANUAL = cargado por el usuario.
    source: str
    verified_name: Optional[str] = None
    status: str
    # Desde qué cuentas se puede usar. Va en la respuesta para que la pantalla
    # pueda mostrarlo y corregirlo: los números MANUAL quedaron enlazados a
    # TODAS las cuentas de la organización al migrar (no había cómo saber de
    # cuál eran), así que acotarlos es un acto del usuario.
    ad_account_ids: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class WhatsAppNumberCreate(BaseModel):
    display_number: str = Field(..., min_length=6, max_length=20,
                                description="Número de WhatsApp sin + ni espacios")
    # Desde qué cuentas se puede usar. `None` = todas las del usuario (lo que
    # hacía siempre); una lista acota. Los números MANUAL no se pueden
    # reconciliar contra Meta, así que su alcance se fija acá.
    ad_account_ids: Optional[List[str]] = None


class WhatsAppNumberUpdate(BaseModel):
    display_number: str = Field(..., min_length=6, max_length=20)
    # `None` deja el alcance como está; una lista lo REEMPLAZA (la pantalla
    # manda el estado final de los checkboxes).
    ad_account_ids: Optional[List[str]] = None

# --- CAMPAIGN SCHEMAS ---
class CampaignSnapshotResponse(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    status: str
    # Estado real de entrega. `status` dice cómo quedó configurada la campaña;
    # esto dice si está entregando. Nullable porque los snapshots anteriores a
    # la migración d4e5f6a7b8c9 no lo tienen.
    effective_status: Optional[str] = None
    budget: float
    spend: float
    impressions: int
    clicks: int
    conversions: int
    cpl: float
    ctr: float
    frequency: float
    placements_breakdown: Dict[str, Any] = {}
    demographics_breakdown: Dict[str, Any] = {}
    extra_breakdowns: Dict[str, Any] = {}
    date: date

    # 4.3 (E-4): agregado real de los últimos 7 días (vía ReportBuilder/cache),
    # no el snapshot de un solo día — el ranking "menor CPL" del dashboard
    # comparaba días individuales, que pueden tener 0 gasto o un pico atípico.
    spend_7d: Optional[float] = None
    conversations_7d: Optional[int] = None
    cost_per_conv_7d: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)

    # Los snapshots del sync real de Meta no rellenan los breakdowns (quedan NULL en
    # DB). Toleramos None y lo normalizamos a {} para no romper la serialización.
    @field_validator("placements_breakdown", "demographics_breakdown", "extra_breakdowns", mode="before")
    @classmethod
    def _none_to_empty_dict(cls, v):
        return v or {}

# --- INSIGHT SCHEMAS ---
class AIInsightResponse(BaseModel):
    id: str
    ad_account_id: str
    campaign_id: Optional[str] = None
    level: str
    category: str
    title: str
    description: str
    suggested_action_type: Optional[str] = None
    is_dismissed: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- CAMPAIGN DRAFT SCHEMAS ---
class CampaignDraftResponse(BaseModel):
    id: str
    name: str
    payload: Dict[str, Any]
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- ACTION SCHEMAS ---
class AIActionResponse(BaseModel):
    id: str
    insight_id: Optional[str] = None
    ad_account_id: str
    action_type: str
    risk_level: str
    title: str
    description: str
    target_campaign_id: Optional[str] = None
    target_adset_id: Optional[str] = None
    platform: str
    parameters: Dict[str, Any]
    status: str
    analysis_run_id: Optional[str] = None
    proposed_at: datetime
    superseded_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    execution_result: Optional[Dict[str, Any]] = None
    estimated_impact: Optional[str] = None
    confidence: float
    # (UX-2) El modelo ya guardaba ambos (ver ActionApprove más abajo y
    # actions.py) pero el response no los exponía — el historial de acciones
    # nunca podía mostrar cuándo ni por qué se rechazó una acción.
    rejected_at: Optional[datetime] = None
    reject_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ActionApprove(BaseModel):
    approve: bool
    reject_reason: Optional[str] = None
