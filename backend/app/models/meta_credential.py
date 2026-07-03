from datetime import datetime, timedelta
import uuid
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from cryptography.fernet import Fernet
from app.config import settings

# Meta: los long-lived user tokens duran ~60 días. Asumimos ese horizonte cuando
# no conocemos la expiración exacta (hasta implementar el intercambio OAuth real).
DEFAULT_TOKEN_TTL_DAYS = 60

class MetaCredential(Base):
    __tablename__ = "meta_credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    access_token_encrypted: Mapped[str] = mapped_column(String)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE") # ACTIVE, EXPIRED, REVOKED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    organization = relationship("Organization", back_populates="meta_credentials")
    ad_accounts = relationship("AdAccount", back_populates="meta_credential")

    def set_access_token(self, token: str, expires_at: datetime = None):
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        self.access_token_encrypted = fernet.encrypt(token.encode()).decode()
        # Si no conocemos la expiración real, asumimos el TTL de long-lived token.
        self.token_expires_at = expires_at or (datetime.utcnow() + timedelta(days=DEFAULT_TOKEN_TTL_DAYS))
        self.status = "ACTIVE"

    def set_refresh_token(self, token: str):
        # Meta no usa refresh tokens (se renueva el long-lived token). Se acepta el
        # parámetro por compatibilidad de la capa de settings, pero no se persiste.
        return None

    def get_access_token(self) -> str:
        if not self.access_token_encrypted:
            return ""
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        return fernet.decrypt(self.access_token_encrypted.encode()).decode()

    @property
    def is_expired(self) -> bool:
        if not self.token_expires_at:
            return False
        return datetime.utcnow() >= self.token_expires_at
