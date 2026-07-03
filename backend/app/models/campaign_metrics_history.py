from datetime import datetime, date
import uuid
from sqlalchemy import String, Float, Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class CampaignMetricsHistory(Base):
    __tablename__ = "campaign_metrics_history"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id: Mapped[str] = mapped_column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)

    # Relaciones
    campaign = relationship("Campaign", back_populates="metrics_history")

    __table_args__ = (
        UniqueConstraint("campaign_id", "date", name="uq_campaign_date"),
    )
