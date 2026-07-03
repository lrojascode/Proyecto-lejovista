from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action_type: Mapped[str] = mapped_column(String) # ej. SYNC_STARTED, CAMPAIGN_CREATED
    meta_endpoint: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String) # SUCCESS, FAILED
    details: Mapped[dict] = mapped_column(JSON, nullable=True) # Detalles adicionales, ids creados, etc.
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relaciones
    organization = relationship("Organization")
    actor = relationship("User")
