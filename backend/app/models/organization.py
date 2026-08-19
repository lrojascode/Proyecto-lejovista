from datetime import datetime
from typing import Optional
import uuid
from sqlalchemy import String, Text, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from cryptography.fernet import Fernet
from app.config import settings
from app.database import Base

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    ai_model: Mapped[str] = mapped_column(String, default="google/gemini-3.5-flash")
    llm_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # openai | gemini_direct | openrouter
    llm_api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    max_budget: Mapped[float] = mapped_column(Float, default=1000.0)  # Límite financiero mensual de seguridad
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    # Rubro del negocio (ver app/services/policy_catalog.py) — determina qué
    # recordatorios de políticas de Meta se muestran en el dashboard.
    industry: Mapped[str] = mapped_column(String, nullable=True)
    # Marca por organización (Fase 6.2 / Roadmap 5)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    accent_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # M-2: overrides parciales por tenant sobre TENANT_CAMPAIGN_RULES /
    # AIThresholds (ver app/tenant_rules/campaign_defaults.py y
    # app/services/ai/thresholds.py). Vacío = usa los defaults globales tal
    # cual — el comportamiento de cualquier org existente no cambia hasta que
    # alguien la personalice explícitamente.
    campaign_rules_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_thresholds_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    users = relationship("OrganizationUser", back_populates="organization", cascade="all, delete-orphan")
    ad_accounts = relationship("AdAccount", back_populates="organization", cascade="all, delete-orphan")
    campaign_snapshots = relationship("CampaignSnapshot", back_populates="organization", cascade="all, delete-orphan")
    insights = relationship("AIInsight", back_populates="organization", cascade="all, delete-orphan")
    actions = relationship("AIAction", back_populates="organization", cascade="all, delete-orphan")
    sync_jobs = relationship("SyncJob", back_populates="organization", cascade="all, delete-orphan")
    meta_credentials = relationship("MetaCredential", back_populates="organization", cascade="all, delete-orphan")
    meta_pages = relationship("MetaPage", back_populates="organization", cascade="all, delete-orphan")
    whatsapp_numbers = relationship("WhatsAppNumber", back_populates="organization", cascade="all, delete-orphan")

    def set_llm_api_key(self, key: Optional[str]) -> None:
        if not key or not key.strip():
            self.llm_api_key_encrypted = None
            return
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        self.llm_api_key_encrypted = fernet.encrypt(key.strip().encode()).decode()

    def get_llm_api_key(self) -> Optional[str]:
        if not self.llm_api_key_encrypted:
            return None
        try:
            fernet = Fernet(settings.ENCRYPTION_KEY.encode())
            return fernet.decrypt(self.llm_api_key_encrypted.encode()).decode()
        except Exception:
            return None

    @property
    def has_llm_api_key(self) -> bool:
        return bool(self.llm_api_key_encrypted)

    @property
    def llm_api_key_preview(self) -> Optional[str]:
        if not self.llm_api_key_encrypted:
            return None
        key = self.get_llm_api_key()
        if not key:
            return None
        if len(key) <= 8:
            return "••••"
        return f"••••••••{key[-4:]}"
