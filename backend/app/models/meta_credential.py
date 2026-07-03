from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from cryptography.fernet import Fernet
from app.config import settings

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

    def set_access_token(self, token: str):
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        self.access_token_encrypted = fernet.encrypt(token.encode()).decode()

    def get_access_token(self) -> str:
        if not self.access_token_encrypted:
            return ""
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        return fernet.decrypt(self.access_token_encrypted.encode()).decode()
