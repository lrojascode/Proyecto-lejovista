from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class MetaPage(Base):
    __tablename__ = "meta_pages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    external_page_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    instagram_actor_id: Mapped[str] = mapped_column(String, nullable=True)
    # Número de WhatsApp conectado a la página (Fase 4 del plan de creación de
    # campañas): permite que la UI ofrezca un dropdown de página en vez de
    # pedir page_id/whatsapp_number a mano, que era la fuente del bug de la
    # reunión ("aparece un código en vez del número").
    whatsapp_number: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    organization = relationship("Organization", back_populates="meta_pages")

    __table_args__ = (
        UniqueConstraint("organization_id", "external_page_id", name="uq_org_meta_page"),
    )
