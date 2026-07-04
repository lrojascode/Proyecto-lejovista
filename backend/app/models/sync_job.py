from datetime import datetime
import uuid
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

# JSONB en Postgres (producción) y JSON genérico en otros dialectos (SQLite en tests).
JSONBType = JSON().with_variant(JSONB, "postgresql")

class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="CASCADE"))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"))
    
    status: Mapped[str] = mapped_column(String, default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED
    sync_type: Mapped[str] = mapped_column(String)  # FULL, INCREMENTAL, WEEKLY
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str] = mapped_column(String, nullable=True)
    records_synced: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[dict] = mapped_column(JSONBType, nullable=True) # Master Brief JSON


    # Relaciones
    organization = relationship("Organization", back_populates="sync_jobs")
