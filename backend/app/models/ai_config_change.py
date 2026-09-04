"""Bitácora de cambios de proveedor y modelo de IA (spec 10, criterio 10).

La spec maestra prohíbe los cambios silenciosos de proveedor o modelo. "No
silencioso" no significa solo que alguien tuvo que pulsar un botón: significa
que después se puede responder *quién lo cambió, cuándo y desde qué valor*.

Sin esta tabla, la única evidencia de que el modelo cambió es un salto en la
factura, y reconstruir la causa exige adivinar. Con ella, un pico de costo o una
regresión de calidad se cruzan contra la fecha del cambio en una consulta.

Se registra tanto el cambio hecho por una persona (`actor_user_id`) como el
hecho por el sistema (`actor_user_id` NULL y `origen` explicando cuál) — por
ejemplo el forzado de un modelo retirado. Un cambio automático sin registro
sería exactamente el cambio silencioso que la spec prohíbe.
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIConfigChange(Base):
    __tablename__ = "ai_config_changes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), index=True
    )

    # `llm_provider`, `ai_model`, `ai_monthly_budget_usd` o `llm_api_key`.
    campo: Mapped[str] = mapped_column(String, index=True)
    valor_anterior: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    valor_nuevo: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Quién. NULL cuando lo hizo el sistema; `origen` dice cuál.
    actor_user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    origen: Mapped[str] = mapped_column(String, default="ui")  # ui | admin | migracion | sistema
    motivo: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
