from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from datetime import datetime, date
from typing import List, Dict, Any, Optional

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
    ai_model: str
    max_budget: float
    status: str
    industry: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    ai_model: Optional[str] = None
    max_budget: Optional[float] = None
    industry: Optional[str] = None

# --- AD ACCOUNT SCHEMAS ---
class AdAccountCreate(BaseModel):
    platform: str # META, GOOGLE
    external_account_id: str
    name: str
    access_token: str
    refresh_token: Optional[str] = None

class AdAccountResponse(BaseModel):
    id: str
    platform: str
    external_account_id: str
    name: str
    status: str
    default_page_id: Optional[str] = None
    spend_cap: float
    amount_spent: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

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

    model_config = ConfigDict(from_attributes=True)


class WhatsAppNumberCreate(BaseModel):
    display_number: str = Field(..., min_length=6, max_length=20,
                                description="Número de WhatsApp sin + ni espacios")


class WhatsAppNumberUpdate(BaseModel):
    display_number: str = Field(..., min_length=6, max_length=20)

# --- CAMPAIGN SCHEMAS ---
class CampaignSnapshotResponse(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    status: str
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
