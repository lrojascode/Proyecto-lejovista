from datetime import datetime, date
import uuid
from sqlalchemy import String, Integer, Float, Text, DateTime, Date, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AdSnapshot(Base):
    __tablename__ = "ad_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)

    campaign_id: Mapped[str] = mapped_column(String, index=True)
    adset_id: Mapped[str] = mapped_column(String, index=True)
    ad_id: Mapped[str] = mapped_column(String, index=True)
    creative_id: Mapped[str] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)

    # Copy real del creativo (título/cuerpo/CTA de Meta) — antes solo se
    # guardaba la imagen (MetaCreative), nunca el texto del anuncio ganador.
    # Permite que la IA se inspire en el copy que ya funcionó, no solo en el
    # estilo genérico del playbook (Fase 3B del plan de creación de campañas).
    creative_title: Mapped[str] = mapped_column(String, nullable=True)
    creative_body: Mapped[str] = mapped_column(Text, nullable=True)
    creative_cta_type: Mapped[str] = mapped_column(String, nullable=True)

    spend: Mapped[float] = mapped_column(Float, default=0.0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)

    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_ad_snapshot_acc_date", "ad_account_id", "date"),
        UniqueConstraint("ad_account_id", "ad_id", "date", name="uq_ad_snapshot_acc_ad_date"),
    )

    ad_account = relationship("AdAccount")
    organization = relationship("Organization")
