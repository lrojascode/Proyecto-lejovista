from datetime import datetime
import uuid
from sqlalchemy import String, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    ai_model: Mapped[str] = mapped_column(String, default="google/gemini-2.5-flash")
    max_budget: Mapped[float] = mapped_column(Float, default=1000.0)  # Límite financiero mensual de seguridad
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    ad_accounts = relationship("AdAccount", back_populates="organization", cascade="all, delete-orphan")
    campaign_snapshots = relationship("CampaignSnapshot", back_populates="organization", cascade="all, delete-orphan")
    insights = relationship("AIInsight", back_populates="organization", cascade="all, delete-orphan")
    actions = relationship("AIAction", back_populates="organization", cascade="all, delete-orphan")
    sync_jobs = relationship("SyncJob", back_populates="organization", cascade="all, delete-orphan")
