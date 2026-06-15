from datetime import datetime
import uuid
from sqlalchemy import String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from cryptography.fernet import Fernet
from app.config import settings

class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"))
    platform: Mapped[str] = mapped_column(String)  # META, GOOGLE
    external_account_id: Mapped[str] = mapped_column(String, index=True)  # act_XXX or Customer ID
    name: Mapped[str] = mapped_column(String)
    access_token_encrypted: Mapped[str] = mapped_column(String)
    refresh_token_encrypted: Mapped[str] = mapped_column(String, nullable=True)
    cached_narrative: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")  # ACTIVE, EXPIRED, PAUSED
    spend_cap: Mapped[float] = mapped_column(Float, default=0.0)
    amount_spent: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    organization = relationship("Organization", back_populates="ad_accounts")
    campaign_snapshots = relationship("CampaignSnapshot", back_populates="ad_account", cascade="all, delete-orphan")
    insights = relationship("AIInsight", back_populates="ad_account", cascade="all, delete-orphan")
    actions = relationship("AIAction", back_populates="ad_account", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("organization_id", "platform", "external_account_id", name="uq_org_platform_acc"),
    )

    def set_access_token(self, token: str):
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        self.access_token_encrypted = fernet.encrypt(token.encode()).decode()

    def get_access_token(self) -> str:
        if not self.access_token_encrypted:
            return ""
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        return fernet.decrypt(self.access_token_encrypted.encode()).decode()

    def set_refresh_token(self, token: str):
        if not token:
            self.refresh_token_encrypted = None
            return
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        self.refresh_token_encrypted = fernet.encrypt(token.encode()).decode()

    def get_refresh_token(self) -> str:
        if not self.refresh_token_encrypted:
            return ""
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        return fernet.decrypt(self.refresh_token_encrypted.encode()).decode()
