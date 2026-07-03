from datetime import datetime
import uuid
from sqlalchemy import String, Integer, DateTime, ForeignKey, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class TenantApiQuota(Base):
    __tablename__ = "tenant_api_quotas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=True, unique=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    business_use_case_usage: Mapped[float] = mapped_column(Float, default=0.0) # Nivel de uso reportado por Meta (0-100)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    organization = relationship("Organization")
    ad_account = relationship("AdAccount")
