from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class OrganizationUser(Base):
    __tablename__ = "organization_users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String, default="VIEWER")  # ADMIN, OPERATOR, VIEWER

    # De que LADO del producto entra este usuario (spec 05).
    #
    # No es un rol mas: los tres roles acotan QUE PUEDE HACER alguien dentro de
    # una organizacion; esto acota A QUE ALTURA DEL PRODUCTO entra. Un ADMIN del
    # cliente y un operador de la agencia pueden tener el mismo rol y necesitan
    # superficies distintas: el cliente no necesita —ni deberia ver— grounding,
    # proveedores de IA, claves, estado de write-back ni diagnosticos de
    # infraestructura.
    #
    # No hay conmutador para cambiarlo desde la sesion: un conmutador convertiria
    # la separacion en una preferencia.
    #
    # `AGENCY` es el default del modelo Y de la migracion a proposito: al
    # desplegar, nadie pierde acceso. Convertir una membresia en `CLIENT` es un
    # acto deliberado de la agencia.
    access_kind: Mapped[str] = mapped_column(String(10), default="AGENCY")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Un usuario pertenece a una organizacion UNA vez. Sin esto, dos
        # aceptaciones concurrentes de la misma invitacion insertaban dos filas
        # y `has_role` leia una sola: cual, dependia del orden de insercion, o
        # sea del azar. La comprobacion "ya existe?" en la aplicacion no alcanza
        # porque las dos transacciones la hacen antes de que ninguna escriba.
        UniqueConstraint("user_id", "organization_id", name="uq_org_user_membership"),
    )

    # Relaciones
    user = relationship("User", back_populates="organizations")
    organization = relationship("Organization", back_populates="users")
