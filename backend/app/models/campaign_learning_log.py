from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CampaignLearningLog(Base):
    """Historial de entrenamiento de la IA en creación de campañas (Ajuste 2A).

    Cada fila registra lo que la IA propuso al crear una campaña — parámetros,
    razonamiento (`analysis_json`), madurez de la cuenta (`maturity_json`) y
    nivel de riesgo — y, más adelante (Fase B), el resultado real tras los días
    de evaluación (`outcome_json`). Es la memoria que permite que la IA aprenda:
    detectar qué funcionó, qué falló, y cuándo dejar de experimentar.

    `status`: PROPOSED (mostrado, aún no creado) → CREATED (Saga lanzada) →
    EVALUATED (resultado real ya medido, Fase B).
    """

    __tablename__ = "campaign_learning_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    draft_id: Mapped[str] = mapped_column(String, ForeignKey("campaign_drafts.id"), nullable=True, index=True)

    product: Mapped[str] = mapped_column(String, nullable=True)
    zone: Mapped[str] = mapped_column(String, nullable=True)

    params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis_json: Mapped[dict] = mapped_column(JSON, default=dict)
    maturity_json: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(String, default="PROPOSED", index=True)

    # Fase B: resultado real tras la ventana de evaluación.
    outcome_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    outcome_recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
