"""Versión inmutable de una propuesta y su autorización de un solo uso (spec 03).

El `CampaignDraft` sigue siendo el contenedor MUTABLE de una solicitud en curso.
Lo que faltaba era el objeto inmutable: hasta ahora la aprobación no estaba atada
a nada: se podía aprobar una propuesta, modificarla y ejecutar una versión
distinta de la autorizada, sin que quedara rastro de la discrepancia. Y no
existía la noción de "consumida", así que la misma aprobación podía dar lugar a
más de una ejecución.

Tres objetos:

* `CampaignProposalVersion` — fotografía cerrada de la configuración. Se aprueba
  una VERSIÓN, no un Draft.
* `CampaignApproval` — autorización ligada a una versión, que pasa a consumida
  en la misma operación que lanza la ejecución.
* `MetaWriteIntent` — el registro de intención que precede a cada escritura real
  hacia Meta, con su clave lógica de idempotencia.
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CampaignProposalVersion(Base):
    """Fotografía cerrada de la configuración de una propuesta.

    Inmutable por contrato: modificar algo material no edita la fila, crea la
    siguiente versión. Es lo que permite decir con precisión QUÉ se aprobó.
    """

    __tablename__ = "campaign_proposal_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id: Mapped[str] = mapped_column(
        String, ForeignKey("campaign_drafts.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str] = mapped_column(String, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, index=True)

    # 1, 2, 3… dentro del Draft. Único por Draft: dos versiones creadas a la vez
    # no pueden compartir número, o "la versión 2" dejaría de identificar una.
    version_number: Mapped[int] = mapped_column(Integer, default=1)

    # La configuración congelada, tal cual se aprobó.
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # Huella de los campos MATERIALES. Dos versiones con la misma huella
    # describen la misma campaña: es lo que distingue un cambio que invalida la
    # aprobación de uno que no (renombrar una nota interna, por ejemplo).
    material_fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    # Resultado de las compuertas RECALCULADAS para esta versión. Se guardan
    # porque la historia 43 pide que cada versión repita presupuesto, riesgo y
    # preflight: sin persistirlo no hay forma de comprobar que se hizo.
    risk_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    gates_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_by_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Por qué se creó esta versión. Vacío en la primera; en las siguientes es el
    # motivo del cambio humano.
    change_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("draft_id", "version_number", name="uq_proposal_version_number"),
    )


# Estados de una autorización.
APPROVAL_PENDING = "PENDING"      # concedida, todavía no ejecutada
APPROVAL_CONSUMED = "CONSUMED"    # ya lanzó su ejecución; no vuelve a servir
APPROVAL_SUPERSEDED = "SUPERSEDED" # una versión posterior la invalidó


class CampaignApproval(Base):
    """Autorización humana sobre UNA versión, consumible una sola vez.

    Aprobar deja de ser una marca sobre el Draft y pasa a ser un objeto con
    estado propio. La ejecución lo consume con un `UPDATE` condicional: dos
    peticiones simultáneas no pueden consumirlo las dos.
    """

    __tablename__ = "campaign_approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id: Mapped[str] = mapped_column(
        String, ForeignKey("campaign_proposal_versions.id", ondelete="CASCADE"), index=True
    )
    draft_id: Mapped[str] = mapped_column(String, index=True)
    organization_id: Mapped[str] = mapped_column(String, index=True)

    status: Mapped[str] = mapped_column(String, default=APPROVAL_PENDING, index=True)
    approved_by_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # Una autorización viva por versión. Aprobar dos veces la misma versión
        # no puede producir dos autorizaciones consumibles.
        UniqueConstraint("version_id", name="uq_approval_por_version"),
    )


# Estados de una escritura hacia Meta. Tres, no dos: la tercera existe para las
# respuestas inciertas y es la única que NO autoriza reintento automático.
WRITE_PENDING = "PENDING"
WRITE_CONFIRMED = "CONFIRMED"
WRITE_VERIFYING = "VERIFYING"


class MetaWriteIntent(Base):
    """Intención de escritura hacia Meta, registrada ANTES de emitirla.

    Es lo que hace la escritura idempotente del lado nuestro: antes del POST se
    registra la intención con su clave lógica; si ya existe un registro con
    resultado, se devuelve ese resultado en vez de volver a escribir.

    También es lo que impide dejar entidades huérfanas sin rastro: si Meta creó
    algo y la respuesta se perdió, el registro previo dice qué se intentó, en qué
    cuenta y bajo qué autorización.
    """

    __tablename__ = "meta_write_intents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Clave lógica: estable, única y derivada de la autorización más el paso.
    # Es la que convierte "reintentar" en "recuperar el resultado anterior".
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)

    organization_id: Mapped[str] = mapped_column(String, index=True)
    ad_account_id: Mapped[str] = mapped_column(String, index=True)
    draft_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    approval_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    # create_campaign | create_adset | create_creative | create_ad | modify_budget…
    operation: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String, default=WRITE_PENDING, index=True)

    # Cómo reconocer en Meta lo que esta intención habría creado, si la
    # respuesta se perdió. Para una campaña es su nombre determinista.
    correlation_tag: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Identificador definitivo que devolvió Meta. Campo de primera clase y no
    # una clave dentro del documento de resultado del Draft: es lo que permite
    # reconstruir qué escribió el sistema y bajo qué autorización.
    external_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
