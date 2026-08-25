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
    # App de Meta que EMITIÓ este token. `None` = la app global de la agencia
    # (`settings.META_APP_ID`), que es el caso normal.
    #
    # Va acá y no en `Organization` a propósito: lo que hay que saber no es "qué
    # app usa este cliente" sino "qué app emitió ESTE token". Una organización
    # puede alojar la cuenta de un cliente conectada con la app de la agencia y
    # la de otro conectada con la suya, y con un solo valor por organización una
    # de las dos queda mal. Y "mal" acá significa que `debug_token` falla, la
    # tarea diaria lee eso como token inválido y marca la cuenta EXPIRED.
    app_id: Mapped[str] = mapped_column(String, nullable=True)
    app_secret_encrypted: Mapped[str] = mapped_column(String, nullable=True)
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

    def set_app_credentials(self, app_id: str = None, app_secret: str = None) -> None:
        """Fija la app que emitió el token. Sin `app_id` vuelve a la app global.

        El secret se cifra con Fernet igual que el access token: es una
        credencial de la misma categoría y guardarla en claro sería peor que no
        soportar apps propias.
        """
        app_id = (app_id or "").strip()
        if not app_id:
            self.app_id = None
            self.app_secret_encrypted = None
            return
        self.app_id = app_id
        if app_secret:
            fernet = Fernet(settings.ENCRYPTION_KEY.encode())
            self.app_secret_encrypted = fernet.encrypt(app_secret.strip().encode()).decode()

    def get_app_secret(self) -> str:
        if not self.app_secret_encrypted:
            return ""
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        return fernet.decrypt(self.app_secret_encrypted.encode()).decode()

    @property
    def app_credentials(self) -> tuple:
        """`(app_id, app_secret)` de esta credencial, o `(None, None)`.

        Devolver la tupla y no dos getters sueltos es para que ningún llamador
        pueda usar el `app_id` propio con el secret global: son un par o no son
        nada.
        """
        if not self.app_id or not self.app_secret_encrypted:
            return (None, None)
        return (self.app_id, self.get_app_secret())

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
