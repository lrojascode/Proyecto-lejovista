"""Tablas de archivo para snapshots viejos (D-3).

campaign_snapshots/adset_snapshots/ad_snapshots crecen a diario y sin límite.
En vez de particionado nativo de Postgres (que exige migrar PKs/constraints de
tablas ya en producción), se usa un archivado periódico simple: las filas más
viejas que `SNAPSHOT_RETENTION_DAYS` se copian a estas tablas "_archive" (sin
FKs, para que sobrevivan aunque se borre el tenant) y se borran de la tabla
caliente. Mismas columnas de negocio; sin índices únicos porque el archivo no
necesita idempotencia, solo lectura ocasional por rango de fecha.

**Contrato de fidelidad (spec 06).** El archivo tiene que conservar TODO lo que
leen el aprendizaje, la explicación y la auditoría. La selección de columnas dejó
de ser un subconjunto elegido a mano: se perdían `objective`, `results`,
`effective_status` y las fechas estructurales — justo los campos que deciden si
dos campañas son comparables y cuántas conversaciones reales hubo. Pasados los
meses de retención en caliente, una campaña histórica dejaba de poder usarse como
evidencia, y el producto perdía su propuesta de valor a medida que envejecía.

La regla operativa: un campo solo puede faltar en el archivo si NINGÚN consumidor
de aprendizaje lo lee, y esa comprobación es parte del cambio, no una revisión
posterior. `tests/test_spec06_baseline.py` falla si se añade una columna a una
tabla caliente sin añadirla aquí.
"""
from datetime import datetime, date
import uuid
from typing import Optional
from sqlalchemy import String, Integer, Float, Text, DateTime, Date, JSON, Index
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
    # Estado EFECTIVO de entrega: distingue una campaña que estaba al aire de una
    # ACTIVE que no entregaba nada. Sin él, el archivo no permite saber si una
    # campaña histórica realmente corrió.
    effective_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Objetivo y calendario: la comparabilidad entre campañas se decide por
    # objetivo y evento (spec 11), y sin ellos el conjunto comparable no se puede
    # construir a partir del archivo.
    objective: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Evento que contaba como resultado (spec 11). El archivo tiene que
    # conservarlo o una campana archivada deja de poder ser evidencia
    # comparable, que es la leccion de ADR-01 corregida en la spec 06.
    optimization_goal: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    stop_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    # Conteo CRUDO de resultados. No se puede derivar de las dos columnas de
    # arriba —`conversations` trae los leads como proxy cuando la campaña no
    # tiene mensajería— así que perderlo es perder la cuenta real.
    results: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
    effective_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    optimization_goal: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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
    effective_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # El copy con el que corrió el anuncio. La comparabilidad considera copy y
    # formato (spec 11): sin esto, un anuncio archivado no se puede comparar con
    # uno nuevo por su creatividad.
    creative_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    creative_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creative_cta_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    results: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
