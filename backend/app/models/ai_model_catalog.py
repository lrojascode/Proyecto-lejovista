"""Catálogo de modelos de IA como DATO administrable (spec 10).

Hasta ahora los modelos admisibles vivían en un diccionario del código
(`MODELS_BY_PROVIDER`) y sus precios en otro (`MODEL_PRICING`). Eso tenía tres
consecuencias:

* Retirar un modelo que el proveedor deja de servir exigía tocar código y
  desplegar, cuando es un cambio de dato.
* Los dos diccionarios podían desalinearse: un modelo elegible sin precio se
  registraba **sin costo estimado**, y el consumo de esa organización quedaba
  contabilizado en cero. Un modelo caro invisible en el panel de gasto.
* No había forma de expresar "este modelo sigue funcionando pero no se debe
  elegir más": era elegible o no existía.

El catálogo unifica las tres cosas —capacidades, precio y estado— en una fila.

`estado` tiene tres valores y la diferencia importa:

* `disponible` — se ofrece en el selector y se puede usar.
* `en_retirada` — ya NO se ofrece a quien no lo tenga, pero las organizaciones
  que lo usan siguen funcionando. Es el estado en el que se avisa.
* `retirado` — no se ofrece y no se puede seleccionar. Las organizaciones que lo
  tuvieran quedan SEÑALADAS (ver `app/services/ai/catalogo.py`), no rotas en
  silencio: el criterio 12 de la spec exige exactamente esa distinción.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
import uuid

from sqlalchemy import Boolean, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ESTADO_DISPONIBLE = "disponible"
ESTADO_EN_RETIRADA = "en_retirada"
ESTADO_RETIRADO = "retirado"

ESTADOS = (ESTADO_DISPONIBLE, ESTADO_EN_RETIRADA, ESTADO_RETIRADO)


class AIModelCatalog(Base):
    """Un modelo admisible de un proveedor, con su precio y su estado."""

    __tablename__ = "ai_model_catalog"
    __table_args__ = (
        UniqueConstraint("provider", "model", name="uq_ai_model_catalog_provider_model"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    provider: Mapped[str] = mapped_column(String, index=True)  # openai | gemini_direct | openrouter
    model: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)

    # Capacidades declaradas. Se consultan antes de mandar una imagen o de pedir
    # `response_format: json_object`: pedirle visión a un modelo que no la tiene
    # devuelve un 400 que no dice cuál es el problema.
    soporta_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    soporta_json: Mapped[bool] = mapped_column(Boolean, default=True)
    soporta_tools: Mapped[bool] = mapped_column(Boolean, default=True)

    # Precio por 1M de tokens, en USD. `Numeric` y no `Float`: alimenta el costo
    # que compara contra el presupuesto de IA, y esa comparación autoriza gasto
    # (spec 02). `precio_cache` es opcional — un modelo sin tarifa de cacheado
    # cobra todo el input a tarifa plena, que SOBRESTIMA, la dirección segura.
    precio_input: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    precio_output: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    precio_cache: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)

    estado: Mapped[str] = mapped_column(String, default=ESTADO_DISPONIBLE, index=True)
    # Por qué se retiró. Se muestra a la agencia cuando una organización queda
    # señalada: "lo retiró el proveedor" y "lo retiramos por caro" se resuelven
    # distinto.
    motivo_retiro: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    orden: Mapped[int] = mapped_column(Numeric(5, 0), default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def elegible(self) -> bool:
        """¿Se puede ELEGIR en el selector? Distinto de si se puede USAR."""
        return self.estado == ESTADO_DISPONIBLE
