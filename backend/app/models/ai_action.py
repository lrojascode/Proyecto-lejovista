from datetime import datetime
import uuid
from sqlalchemy import String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class AIAction(Base):
    __tablename__ = "ai_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    insight_id: Mapped[str] = mapped_column(String, ForeignKey("ai_insights.id", ondelete="SET NULL"), nullable=True)
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"))
    
    action_type: Mapped[str] = mapped_column(String)  # MODIFY_BUDGET, PAUSE_CAMPAIGN, CREATE_CAMPAIGN, etc.
    risk_level: Mapped[str] = mapped_column(String)  # LOW, MEDIUM, HIGH
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    
    target_campaign_id: Mapped[str] = mapped_column(String, nullable=True)
    target_adset_id: Mapped[str] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, default="META")  # META, GOOGLE
    
    # Parámetros y llamadas API
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    planned_api_calls: Mapped[list] = mapped_column(JSON, default=list)
    
    # Ciclo de vida
    status: Mapped[str] = mapped_column(String, default="PROPOSED")  # PROPOSED, APPROVED, REJECTED, EXECUTING, COMPLETED, FAILED
    proposed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str] = mapped_column(String, nullable=True)  # User ID o "AUTO"
    rejected_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str] = mapped_column(String, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    execution_result: Mapped[dict] = mapped_column(JSON, nullable=True)
    
    # Estimaciones
    estimated_impact: Mapped[str] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # Relaciones
    insight = relationship("AIInsight", back_populates="actions")
    ad_account = relationship("AdAccount", back_populates="actions")
    organization = relationship("Organization", back_populates="actions")
