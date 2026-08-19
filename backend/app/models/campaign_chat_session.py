from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, JSON, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CampaignChatSession(Base):
    """Sesión del asistente conversacional de creación de campañas (CH-1..CH-5).

    Es al chat lo que `CampaignDraft.payload` es a la Saga: el estado de un
    proceso multi-turno vive en columnas JSON en vez de en tablas propias — la
    transcripción y las respuestas acumuladas se leen siempre enteras y se
    escriben con cada turno, no se filtran ni paginan por separado.

    `state` es el cursor conversacional (`AWAITING_MEDIA`, `REVIEW_PROPOSAL`,
    `DONE`, …) que gobierna `next_state()`. `status` es el ciclo de vida de la
    sesión para listados/expiración, deliberadamente separado de `state`.

    `draft_id` es la garantía dura de idempotencia: UNIQUE + nullable, así que
    una sesión crea como máximo una campaña real. El chequeo de estado en
    memoria no alcanza contra un doble clic o un reintento de red.
    """

    __tablename__ = "campaign_chat_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Congelada al crear la sesión: un cambio de X-Ad-Account-Id a mitad de
    # conversación no debe mover la sesión a otra cuenta.
    ad_account_id: Mapped[str] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    state: Mapped[str] = mapped_column(String, nullable=False, default="AWAITING_MEDIA", index=True)
    # OPEN | CLOSED_CREATED | CLOSED_CANCELLED | CLOSED_FAILED | CLOSED_EXPIRED
    status: Mapped[str] = mapped_column(String, nullable=False, default="OPEN", index=True)
    # BUILD (creación de campañas) | ASK (consulta / analista)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="BUILD", default="BUILD")

    slots_json: Mapped[dict] = mapped_column(JSON, default=dict)
    proposal_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    preflight_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    risk_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    messages_json: Mapped[list] = mapped_column(JSON, default=list)

    draft_id: Mapped[str] = mapped_column(
        String, ForeignKey("campaign_drafts.id", ondelete="SET NULL"), nullable=True
    )

    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    last_client_turn_id: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True
    )

    __table_args__ = (
        UniqueConstraint("draft_id", name="uq_campaign_chat_sessions_draft_id"),
    )
