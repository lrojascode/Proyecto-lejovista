"""Descartes de salidas del modelo (spec 10, criterios 1–4 y activación).

Un descarte hasta ahora era una línea de log. Eso alcanza para depurar un caso
puntual y no alcanza para dos cosas que la spec sí exige:

* **Registrar el descarte** (criterio 1). Un `logger.warning` se rota y se
  pierde; no se puede responder "cuántas respuestas se descartaron por cifra sin
  respaldo la semana pasada" sin volver a leer archivos que ya no están.
* **El modo de registro** de la activación. La spec pide desplegar el
  endurecimiento del grounding *midiendo qué descartaría sobre tráfico real
  antes de descartar*. Eso es imposible con logs: hay que poder contar, agrupar
  por motivo y comparar contra el volumen total.

`aplicado` es la columna que separa las dos cosas. En modo de registro se
escribe la fila con `aplicado=False`: la respuesta SE ENTREGA y queda anotado que
la guarda la habría descartado. Con el descarte activo, `aplicado=True`.

Se guarda el motivo y un extracto acotado, nunca la respuesta completa: el texto
del modelo puede llevar datos del cliente, y esta tabla existe para medir la
guarda, no para archivar conversaciones.
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Motivos. Se corresponden uno a uno con los criterios 1–4 de la spec, más los
# que la guarda ya aplicaba (enlace, longitud, respuesta vacía).
MOTIVO_CIFRA_SIN_RESPALDO = "cifra_sin_respaldo"
MOTIVO_IDENTIFICADOR_AJENO = "identificador_ajeno"
MOTIVO_PERIODO_CAMBIADO = "periodo_cambiado"
MOTIVO_CUENTA_AJENA = "cuenta_ajena"
MOTIVO_FUERA_DE_CATALOGO = "fuera_de_catalogo"
MOTIVO_PROMESA_DE_EXITO = "promesa_de_exito"
MOTIVO_CONTRATO_INVALIDO = "contrato_invalido"
MOTIVO_ENLACE = "enlace"
MOTIVO_DEMASIADO_LARGA = "demasiado_larga"
MOTIVO_VACIA = "vacia"

# Cuánto texto se conserva para poder investigar sin archivar la conversación.
MAX_EXTRACTO = 400


class AIDiscard(Base):
    __tablename__ = "ai_discards"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=True, index=True
    )
    ad_account_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    feature: Mapped[str] = mapped_column(String, index=True)
    motivo: Mapped[str] = mapped_column(String, index=True)
    # El valor concreto que no tenía respaldo, o el campo que rompió el
    # contrato. Es lo primero que se mira al investigar un falso positivo.
    detalle: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracto: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # False = modo de registro: la guarda habría descartado y NO descartó.
    aplicado: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
