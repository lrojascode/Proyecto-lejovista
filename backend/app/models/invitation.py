from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="OPERATOR")
    # Lo que se guardaba ANTES era el token en claro: cualquiera con lectura
    # sobre esta tabla —un dump, un backup, un log de consultas— podia entrar
    # como el invitado. Ahora se guarda su SHA-256, igual que
    # `password_reset_tokens`: lo persistido no sirve para aceptar nada.
    #
    # `token` se conserva NULLABLE durante la fase de expansion de la migracion
    # y deja de leerse; se retira en la contraccion.
    token: Mapped[str] = mapped_column(String(100), unique=True, nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    invited_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Cuentas publicitarias que se le conceden al aceptar (ver UserAdAccount).
    # El modelo de concesiones es fail-closed: sin esto, un invitado nuevo entra
    # y no ve ninguna cuenta hasta que un ADMIN se acuerde de dárselas. La
    # decisión de a qué accede se toma donde se invita.
    ad_account_ids: Mapped[list] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    organization = relationship("Organization")
    invited_by = relationship("User")

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None


def hash_invitation_token(token: str) -> str:
    """SHA-256 del token de invitacion.

    Mismo criterio que el hash de los tokens de recuperacion de contrasena: la
    tabla de invitaciones es un objetivo tan bueno como `users`, y lo que se
    guarda no puede ser suficiente para entrar.

    SHA-256 sin sal ni estiramiento es lo correcto aqui y no en una contrasena:
    el token son 32 bytes aleatorios de `secrets`, no hay diccionario que
    recorrer.
    """
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()
