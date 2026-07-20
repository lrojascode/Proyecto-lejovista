from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class WhatsAppNumber(Base):
    """Catálogo de números de WhatsApp de la organización (F1).

    `MetaPage.whatsapp_number` guarda UN número por página y sigue siendo el
    predeterminado de esa página; esta tabla es la lista de la que se elige al
    crear una campaña, y admite varios.

    Dos orígenes conviven a propósito:
    - `META`: descubiertos vía WhatsApp Business API. Los reescribe el sync, así
      que no se editan ni borran a mano.
    - `MANUAL`: cargados por el usuario. Son el camino real hoy, porque el
      descubrimiento automático necesita el permiso `whatsapp_business_management`
      que la mayoría de tokens conectados no tiene.
    """
    __tablename__ = "whatsapp_numbers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # Número tal como se muestra y se usa (ej. "51999888777"). El deep link de
    # WhatsApp del creativo le quita todo lo que no sea dígito.
    display_number: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="MANUAL")  # META | MANUAL
    # id del phone number en Meta. Solo para los de origen META; es la clave con
    # la que el sync los reconoce y actualiza en vez de duplicarlos.
    external_id: Mapped[str] = mapped_column(String, nullable=True)
    verified_name: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="whatsapp_numbers")

    __table_args__ = (
        # Un mismo número no se lista dos veces en la organización...
        UniqueConstraint("organization_id", "display_number", name="uq_org_whatsapp_number"),
        # ...ni se duplica el mismo phone number de Meta.
        UniqueConstraint("organization_id", "external_id", name="uq_org_whatsapp_external"),
    )
