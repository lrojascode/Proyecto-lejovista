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
    meta_credential_id: Mapped[str] = mapped_column(String, ForeignKey("meta_credentials.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String)  # META, GOOGLE
    external_account_id: Mapped[str] = mapped_column(String, index=True)  # act_XXX or Customer ID
    name: Mapped[str] = mapped_column(String)
    cached_narrative: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")  # ACTIVE, EXPIRED, PAUSED
    spend_cap: Mapped[float] = mapped_column(Float, default=0.0)
    amount_spent: Mapped[float] = mapped_column(Float, default=0.0)
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

    def set_access_token(self, token: str):
        if not self.meta_credential:
            from app.models.meta_credential import MetaCredential
            self.meta_credential = MetaCredential()
        self.meta_credential.set_access_token(token)

    def set_refresh_token(self, token: str):
        if not self.meta_credential:
            from app.models.meta_credential import MetaCredential
            self.meta_credential = MetaCredential()
        self.meta_credential.set_refresh_token(token)

    __table_args__ = (
        UniqueConstraint("organization_id", "platform", "external_account_id", name="uq_org_platform_acc"),
    )
