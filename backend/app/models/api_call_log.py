from datetime import datetime
import uuid
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class APICallLog(Base):
    __tablename__ = "api_call_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True)
    method: Mapped[str] = mapped_column(String)  # GET, POST
    endpoint: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status_code: Mapped[int] = mapped_column(Integer)
    response_preview: Mapped[str] = mapped_column(String, nullable=True)  # Vista previa o resumen
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
