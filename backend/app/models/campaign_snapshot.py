from datetime import datetime, date
import uuid
from sqlalchemy import String, Integer, Float, DateTime, Date, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class CampaignSnapshot(Base):
    __tablename__ = "campaign_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id"))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"))
    
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    campaign_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)  # ACTIVE, PAUSED, ARCHIVED
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)  # Leads
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    frequency: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Breakdowns en formato JSON
    placements_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    demographics_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    ad_account = relationship("AdAccount", back_populates="campaign_snapshots")
    organization = relationship("Organization", back_populates="campaign_snapshots")
