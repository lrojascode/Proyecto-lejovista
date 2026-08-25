"""Token de un solo uso para recuperar la contraseña.

Sin esto, un cliente que pierde la clave no tiene salida: no hay flujo de
recuperación ni de cambio, así que la única forma de volver a entrar es que
alguien le edite el hash en la base a mano.

Se modela como tabla y no como JWT firmado a propósito: hay que poder
**invalidarlo** en cuanto se usa (o cuando se pide uno nuevo), y un JWT sin
estado no se puede revocar. Es el mismo criterio que ya se tomó para los refresh
tokens, que se revocan por `jti` en Redis.

El token se guarda **hasheado**, igual que una contraseña: la tabla es un
objetivo tan bueno como `users`, y un token de recuperación en claro es una
sesión regalada. Lo que viaja por correo es el valor original, que no se
persiste en ningún lado.
"""
from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

# Corto a propósito: es una ventana de ataque abierta en el correo de alguien.
RESET_TTL_MINUTES = 30


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # SHA-256 del token que viajó por correo. Ver el docstring del módulo.
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User")

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_usable(self) -> bool:
        return not self.is_expired and not self.is_used
