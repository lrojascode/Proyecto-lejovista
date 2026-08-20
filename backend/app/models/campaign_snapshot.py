from datetime import datetime, date
from typing import Optional
import uuid
from sqlalchemy import String, Integer, Float, DateTime, Date, ForeignKey, JSON, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class CampaignSnapshot(Base):
    __tablename__ = "campaign_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    campaign_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)  # ACTIVE, PAUSED, ARCHIVED
    # Datos estructurales de Meta (objetivo declarado y calendario de la campaña).
    # Nullable: Meta no siempre los devuelve, y un campo ausente no puede romper el sync.
    objective: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    stop_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int] = mapped_column(Integer, default=0)  # personas únicas alcanzadas
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)  # Leads
    conversations: Mapped[int] = mapped_column(Integer, default=0)  # mensajes iniciados (WhatsApp)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    frequency: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Breakdowns en formato JSON
    placements_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    demographics_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    # Breakdowns adicionales (género, dispositivo, geografía) — P-2.
    extra_breakdowns: Mapped[dict] = mapped_column(JSON, default=dict)
    
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_snapshot_org_date", "organization_id", "date"),
        # Query dominante del AIEngine: filtra por (ad_account_id, date >= X)
        Index("idx_snapshot_acc_date", "ad_account_id", "date"),
        # Idempotencia: un único snapshot por campaña y día en cada cuenta
        UniqueConstraint("ad_account_id", "campaign_id", "date", name="uq_snapshot_acc_campaign_date"),
    )

    # Relaciones
    ad_account = relationship("AdAccount", back_populates="campaign_snapshots")
    organization = relationship("Organization", back_populates="campaign_snapshots")
