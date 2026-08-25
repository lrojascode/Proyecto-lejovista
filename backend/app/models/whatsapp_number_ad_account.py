"""Qué cuenta publicitaria puede usar qué número de WhatsApp.

Mismo problema y misma forma que `MetaPageAdAccount`: `WhatsAppNumber` es por
ORGANIZACIÓN, así que en una organización que aloja varios clientes el selector
de número de "Crear campaña" mezclaba los de todos — y un número de WhatsApp
ajeno en un anuncio no es un dato de más, es que los mensajes del cliente A le
lleguen al B.

Los de origen META los descubre `_sync_whatsapp_numbers` recorriendo los WABA
del negocio DUEÑO de la cuenta que se sincroniza, o sea que ya vienen por
cuenta. Los MANUAL se enlazan a la cuenta activa de quien los carga.
"""
from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class WhatsAppNumberAdAccount(Base):
    __tablename__ = "whatsapp_number_ad_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    whatsapp_number_id: Mapped[str] = mapped_column(
        String, ForeignKey("whatsapp_numbers.id", ondelete="CASCADE"), index=True
    )
    ad_account_id: Mapped[str] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    whatsapp_number = relationship("WhatsAppNumber")
    ad_account = relationship("AdAccount")

    __table_args__ = (
        UniqueConstraint(
            "whatsapp_number_id", "ad_account_id", name="uq_whatsapp_number_ad_account"
        ),
    )
