from datetime import datetime
import uuid
from sqlalchemy import String, Text, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from cryptography.fernet import Fernet
from app.config import settings

class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id", ondelete="CASCADE"))
    meta_credential_id: Mapped[str] = mapped_column(String, ForeignKey("meta_credentials.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String)  # META, GOOGLE
    external_account_id: Mapped[str] = mapped_column(String, index=True)  # act_XXX or Customer ID
    name: Mapped[str] = mapped_column(String)
    # Narrativa del Master Brief cacheada + metadatos de trazabilidad (D-4)
    cached_narrative: Mapped[str] = mapped_column(Text, nullable=True)
    narrative_generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    narrative_model: Mapped[str] = mapped_column(String, nullable=True)
    # Proveedor real que generó la narrativa (openai/gemini_direct/openrouter, ver
    # OpenRouterClient.provider) — narrative_model por sí solo es ambiguo entre
    # proveedores (AI-4, ver HANDOFF hallazgo 3.4/AI-4).
    narrative_provider: Mapped[str] = mapped_column(String, nullable=True)
    narrative_prompt_version: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")  # ACTIVE, EXPIRED, PAUSED
    # Página de Facebook predeterminada para las campañas de esta cuenta.
    # La usa AIExecutor para resolver página+WhatsApp sin caer en NEEDS_INPUT
    # cuando la org tiene más de una página, y el formulario de creación para
    # preseleccionar. Se configura en Settings ("Páginas y WhatsApp").
    default_page_id: Mapped[str] = mapped_column(
        String, ForeignKey("meta_pages.id", ondelete="SET NULL"), nullable=True
    )
    spend_cap: Mapped[float] = mapped_column(Float, default=0.0)
    amount_spent: Mapped[float] = mapped_column(Float, default=0.0)
    last_sync_error_code: Mapped[int] = mapped_column(nullable=True)
    last_sync_error_detail: Mapped[str] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    organization = relationship("Organization", back_populates="ad_accounts")
    meta_credential = relationship("MetaCredential", back_populates="ad_accounts", lazy="selectin")
    campaign_snapshots = relationship("CampaignSnapshot", back_populates="ad_account", cascade="all, delete-orphan")
    insights = relationship("AIInsight", back_populates="ad_account", cascade="all, delete-orphan")
    actions = relationship("AIAction", back_populates="ad_account", cascade="all, delete-orphan")

    def get_access_token(self):
        if self.meta_credential:
            return self.meta_credential.get_access_token()
        return None

    def set_access_token(self, token: str, expires_at=None, never_expires: bool = False):
        if not self.meta_credential:
            from app.models.meta_credential import MetaCredential
            self.meta_credential = MetaCredential(organization_id=self.organization_id)
        self.meta_credential.set_access_token(
            token, expires_at=expires_at, never_expires=never_expires
        )

    def set_refresh_token(self, token: str):
        if not self.meta_credential:
            from app.models.meta_credential import MetaCredential
            self.meta_credential = MetaCredential(organization_id=self.organization_id)
        self.meta_credential.set_refresh_token(token)

    @property
    def app_id(self):
        """App de Meta que emitió el token de esta cuenta, o `None` si usa la
        global de la agencia. Se expone (solo el ID, nunca el secret) para que
        Configuración pueda mostrar cuál está en uso."""
        if self.meta_credential:
            return self.meta_credential.app_id
        return None

    @property
    def token_expires_at(self):
        if self.meta_credential:
            return self.meta_credential.token_expires_at
        return None

    @property
    def token_expires_in_days(self):
        if self.meta_credential and self.meta_credential.token_expires_at:
            delta = self.meta_credential.token_expires_at - datetime.utcnow()
            return max(0, delta.days)
        return None

    @property
    def token_warning(self) -> bool:
        if self.status == "EXPIRED":
            return True
        days = self.token_expires_in_days
        if days is not None and days <= 7:
            return True
        return False

    __table_args__ = (
        UniqueConstraint("organization_id", "platform", "external_account_id", name="uq_org_platform_acc"),
    )
