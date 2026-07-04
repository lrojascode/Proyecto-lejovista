from datetime import datetime
import uuid
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class CustomAudience(Base):
    """Audiencias personalizadas de la cuenta (customaudiences)."""
    __tablename__ = "custom_audiences"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)

    external_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    subtype: Mapped[str] = mapped_column(String, nullable=True)  # CUSTOM, LOOKALIKE, WEBSITE, etc.
    description: Mapped[str] = mapped_column(String, nullable=True)
    approximate_count: Mapped[int] = mapped_column(Integer, nullable=True)
    meta_time_created: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ad_account_id", "external_id", name="uq_audience_acc_external"),
    )

    ad_account = relationship("AdAccount")
    organization = relationship("Organization")
