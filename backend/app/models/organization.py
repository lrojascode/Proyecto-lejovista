from datetime import datetime
import uuid
from sqlalchemy import String, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    ai_model: Mapped[str] = mapped_column(String, default="google/gemini-3.5-flash")
    max_budget: Mapped[float] = mapped_column(Float, default=1000.0)  # Límite financiero mensual de seguridad
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    # Rubro del negocio (ver app/services/policy_catalog.py) — determina qué
    # recordatorios de políticas de Meta se muestran en el dashboard.
    industry: Mapped[str] = mapped_column(String, nullable=True)
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
