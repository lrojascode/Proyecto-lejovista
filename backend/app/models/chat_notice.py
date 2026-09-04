"""Registro de avisos proactivos entregados (spec 12, criterio 17).

Existe por una razón sola: **que el mismo aviso no se repita en cada apertura**.
Sin registro, un hallazgo que sigue vigente —una campaña cara lo es todos los
días hasta que alguien la toque— se entregaría cada vez que el cliente abre el
chat, y en tres aperturas deja de leerse. Con él se pierde también el aviso que
sí importaba, que es el daño de verdad.

Se guarda la HUELLA del aviso, no su texto. El texto cambia con las cifras del
día; lo que decide si es el mismo aviso es de qué habla: qué cuenta, qué
campaña y de qué tipo. Comparar textos haría que un céntimo de diferencia lo
convirtiera en un aviso nuevo.
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatNoticeLog(Base):
    __tablename__ = "chat_notice_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    ad_account_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # A quién se le entregó. Dos personas de la misma organización tienen
    # derecho a ver el aviso cada una: silenciarlo para la segunda porque la
    # primera ya lo vio esconde el hallazgo de quien puede actuar.
    user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # De qué habla: `{categoria}:{campaign_id o cuenta}`. Ver el módulo `avisos`.
    huella: Mapped[str] = mapped_column(String, index=True)
    categoria: Mapped[str] = mapped_column(String, index=True)
    nivel: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    insight_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    entregado_en: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
