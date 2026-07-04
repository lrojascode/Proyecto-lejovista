from pydantic import BaseModel, EmailStr, ConfigDict, field_validator
from datetime import datetime, date
from typing import List, Dict, Any, Optional

# --- AUTH SCHEMAS ---
class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    organization_slug: str # Crear org si no existe, o asignar
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
    access_token: str
    token_type: str
    user: UserResponse
    refresh_token: Optional[str] = None

class RefreshRequest(BaseModel):
    refresh_token: str

# --- ORGANIZATION SCHEMAS ---
class OrganizationResponse(BaseModel):
    id: str
    slug: str
    name: str
    ai_model: str
    max_budget: float
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    ai_model: Optional[str] = None
    max_budget: Optional[float] = None

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
    spend_cap: float
    amount_spent: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

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
    proposed_at: datetime
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    execution_result: Optional[Dict[str, Any]] = None
    estimated_impact: Optional[str] = None
    confidence: float

    model_config = ConfigDict(from_attributes=True)

class ActionApprove(BaseModel):
    approve: bool
    reject_reason: Optional[str] = None
