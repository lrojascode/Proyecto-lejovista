from datetime import datetime
import uuid
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AITokenUsage(Base):
    """Registro de consumo de tokens de cada llamada real al LLM.

    Existe para medir en qué se gasta la IA: cada fila es una llamada al modelo,
    etiquetada por `feature` (la funcionalidad/página que la originó, p. ej.
    'master_brief', 'campaign_copy', 'campaign_analysis'), de modo que el gasto
    se pueda desglosar por funcionalidad. `reasoning_tokens` viene del bloque
    `completion_tokens_details` que devuelven los modelos con cadena de
    pensamiento; `used_cot` es True cuando ese valor es > 0.

    Solo se graban llamadas REALES: en modo MOCK / sin API key no hay consumo
    que medir, así que no se registra nada.
    """

    __tablename__ = "ai_token_usage"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True)

    # Funcionalidad/página que originó la llamada (desglose "por página").
    feature: Mapped[str] = mapped_column(String, index=True)
    provider: Mapped[str] = mapped_column(String)  # openai, gemini_direct, openrouter
    model: Mapped[str] = mapped_column(String)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)  # cadena de pensamiento
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    used_cot: Mapped[bool] = mapped_column(Boolean, default=False)

    # Costo estimado en USD según la tabla de precios por modelo (token_tracking.py).
    # Nullable: si el modelo no está en la tabla, se deja sin estimar en vez de mentir un 0.
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
