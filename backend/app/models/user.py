from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    full_name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    organizations = relationship("OrganizationUser", back_populates="user", cascade="all, delete-orphan")

    @property
    def organization_id(self) -> str | None:
        # Organización ACTIVA de la sesión, fijada por get_current_user desde el
        # claim del token o el header X-Tenant-Slug (S-8). Si no, la primera.
        active = getattr(self, "_active_org_id", None)
        if active:
            return active
        if self.organizations:
            return self.organizations[0].organization_id
        return None
