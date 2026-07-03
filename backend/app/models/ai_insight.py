from datetime import datetime
import uuid
from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class AIInsight(Base):
    __tablename__ = "ai_insights"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id"), index=True)
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    
    level: Mapped[str] = mapped_column(String)  # INFO, WARNING, CRITICAL
    category: Mapped[str] = mapped_column(String)  # BUDGET, PLACEMENT, SATURATION, PERFORMANCE, ACCOUNT
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    
    suggested_action_type: Mapped[str] = mapped_column(String, nullable=True)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    ad_account = relationship("AdAccount", back_populates="insights")
    organization = relationship("Organization", back_populates="insights")
    actions = relationship("AIAction", back_populates="insight", cascade="all, delete-orphan")
