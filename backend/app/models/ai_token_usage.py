from datetime import datetime
from typing import Optional
import uuid
from decimal import Decimal
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Numeric
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
    # Versión del prompt que originó la llamada (spec 10, criterio 7). Sin ella,
    # una regresión de calidad después de tocar un prompt no se puede acotar en
    # el tiempo: se ve el modelo y el costo, pero no QUÉ instrucciones recibió.
    # Nullable por las filas anteriores a esta spec, que no la tienen.
    prompt_version: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)  # cadena de pensamiento
    # Input servido desde la cache del proveedor, ~10x mas barato. Viene DENTRO
    # de `prompt_tokens`, no aparte: sumarlo al total contaria doble.
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    used_cot: Mapped[bool] = mapped_column(Boolean, default=False)

    # Costo estimado en USD según el catálogo de modelos (ai/catalogo.py).
    # Nullable: si el modelo no tiene tarifa, se deja sin estimar en vez de mentir un 0.
    #
    # `estimated_cost_usd` (Float) se conserva durante la fase de EXPANSIÓN: lo
    # consumen el panel de consumo y la imagen anterior. Lo que DECIDE es
    # `estimated_cost_usd_exact`, porque esta suma es la que compara contra el
    # presupuesto mensual de IA y decide si se bloquean las llamadas — o sea,
    # autoriza gasto, y eso no se resuelve en coma flotante (spec 02).
    #
    # Seis decimales y no dos: una llamada barata cuesta fracciones de centavo, y
    # redondear cada fila a dos decimales haría que miles de llamadas sumaran
    # cero. La resolución tiene que ser la del precio por token, no la de la
    # moneda.
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_cost_usd_exact: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 6), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
