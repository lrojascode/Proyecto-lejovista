from datetime import datetime
import uuid
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AccountLearningSnapshot(Base):
    """Memoria longitudinal del rendimiento REAL de una cuenta, capturada en
    cada sincronización (Q2).

    El `CampaignLearningLog` aprende de las campañas que la IA *crea* (propone →
    crea → mide outcome). Esto complementa esa memoria con la otra mitad: qué
    pasó de verdad con TODA la cuenta cada vez que se traen datos frescos de
    Meta — incluyendo campañas creadas a mano. Todo determinista (regla AI-1,
    sin LLM ni cifras inventadas): KPIs medidos de la ventana, su variación vs la
    sincronización anterior, ganadoras/perdedoras, el segmento más eficiente y un
    veredicto de tendencia. Es lo que permite leer la trayectoria real de la
    cuenta a lo largo del tiempo, no solo la última foto.

    `verdict`: IMPROVING / STABLE / DEGRADING / NO_DATA / BASELINE (primera vez).
    """

    __tablename__ = "account_learning_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True)

    window_days: Mapped[int] = mapped_column(Integer, default=7)

    # KPIs medidos de la ventana: {spend, conversations, cost_per_conv}.
    kpis_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Variación % vs el snapshot anterior de esta cuenta: {spend, conversations, cost_per_conv}.
    deltas_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Mejores/peores campañas por costo/conversación en la ventana.
    top_json: Mapped[list] = mapped_column(JSON, default=list)
    bottom_json: Mapped[list] = mapped_column(JSON, default=list)
    # Segmento más eficiente por grupo: {placement, device, age} → {name, cost_per_conv}.
    segmentation_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Aprendizajes deterministas en lenguaje llano (list[str]).
    observations_json: Mapped[list] = mapped_column(JSON, default=list)

    verdict: Mapped[str] = mapped_column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
