from datetime import datetime
import uuid
from sqlalchemy import String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AdAccountSpendMonthly(Base):
    """Gasto total por cuenta y mes calendario (sin desglose por campaña).

    `closed=True` marca un mes ya cerrado: el sync incremental nunca lo vuelve
    a tocar, solo actualiza el mes en curso. El backfill (`time_increment=monthly`
    contra Meta) solo corre una vez por cuenta — ver MetaAdsSyncService."""
    __tablename__ = "ad_account_spend_monthly"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String, index=True)
    year_month: Mapped[str] = mapped_column(String)  # "YYYY-MM"
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ad_account_id", "year_month", name="uq_ad_account_year_month"),
    )
