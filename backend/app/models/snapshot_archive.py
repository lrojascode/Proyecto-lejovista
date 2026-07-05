"""Tablas de archivo para snapshots viejos (D-3).

campaign_snapshots/adset_snapshots/ad_snapshots crecen a diario y sin límite.
En vez de particionado nativo de Postgres (que exige migrar PKs/constraints de
tablas ya en producción), se usa un archivado periódico simple: las filas más
viejas que `SNAPSHOT_RETENTION_DAYS` se copian a estas tablas "_archive" (sin
FKs, para que sobrevivan aunque se borre el tenant) y se borran de la tabla
caliente. Mismas columnas de negocio; sin índices únicos porque el archivo no
necesita idempotencia, solo lectura ocasional por rango de fecha.
"""
from datetime import datetime, date
import uuid
from sqlalchemy import String, Integer, Float, DateTime, Date, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CampaignSnapshotArchive(Base):
    __tablename__ = "campaign_snapshots_archive"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    original_id: Mapped[str] = mapped_column(String, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, index=True)
    organization_id: Mapped[str] = mapped_column(String, index=True)
    campaign_id: Mapped[str] = mapped_column(String)
    campaign_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    frequency: Mapped[float] = mapped_column(Float, default=0.0)
    placements_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    demographics_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    extra_breakdowns: Mapped[dict] = mapped_column(JSON, default=dict)
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_campaign_snapshot_archive_acc_date", "ad_account_id", "date"),
    )


class AdsetSnapshotArchive(Base):
    __tablename__ = "adset_snapshots_archive"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    original_id: Mapped[str] = mapped_column(String, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, index=True)
    organization_id: Mapped[str] = mapped_column(String, index=True)
    campaign_id: Mapped[str] = mapped_column(String)
    adset_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    targeting_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_adset_snapshot_archive_acc_date", "ad_account_id", "date"),
    )


class AdSnapshotArchive(Base):
    __tablename__ = "ad_snapshots_archive"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    original_id: Mapped[str] = mapped_column(String, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, index=True)
    organization_id: Mapped[str] = mapped_column(String, index=True)
    campaign_id: Mapped[str] = mapped_column(String)
    adset_id: Mapped[str] = mapped_column(String)
    ad_id: Mapped[str] = mapped_column(String)
    creative_id: Mapped[str] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_ad_snapshot_archive_acc_date", "ad_account_id", "date"),
    )
