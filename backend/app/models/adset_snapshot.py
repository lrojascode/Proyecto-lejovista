from datetime import datetime, date
from typing import Optional
import uuid
from sqlalchemy import String, Integer, Float, DateTime, Date, ForeignKey, JSON, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AdsetSnapshot(Base):
    __tablename__ = "adset_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)

    campaign_id: Mapped[str] = mapped_column(String, index=True)
    adset_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)

    # Presupuesto diario del adset (nominal, no en centavos). En cuentas ABO el
    # presupuesto vive a nivel adset; se usa para derivar el de la campaña (Bug 5).
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)

    # Resumen del targeting (geo, edad, placements, tipo de audiencia)
    targeting_summary: Mapped[dict] = mapped_column(JSON, default=dict)

    # Fecha de fin del adset en Meta. Nullable: no siempre viene, y su ausencia
    # no puede romper el sync.
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_adset_snapshot_acc_date", "ad_account_id", "date"),
        UniqueConstraint("ad_account_id", "adset_id", "date", name="uq_adset_snapshot_acc_adset_date"),
    )

    ad_account = relationship("AdAccount")
    organization = relationship("Organization")
