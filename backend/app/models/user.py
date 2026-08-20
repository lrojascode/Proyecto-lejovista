from datetime import datetime
import uuid
from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    full_name: Mapped[str] = mapped_column(String)
    # Administrador de PLATAFORMA. No es un rol de organizacion: los tres
    # roles de OrganizationUser (ADMIN/OPERATOR/VIEWER) son SIEMPRE dentro de
    # una organizacion, y un ADMIN del Cliente A no puede crear el Cliente B.
    #
    # Regla dura: este flag NO da acceso a los datos de ningun tenant. Permite
    # crear organizaciones, invitar a su primer ADMIN y prender o apagar
    # modulos. NO permite leer campanas, gastos ni el chat de un cliente. El
    # aislamiento por organizacion es una de las reglas innegociables del
    # proyecto, y un rol que la saltee en silencio la deroga de hecho.
    #
    # Si algun dia hace falta soporte tecnico con acceso a datos, eso es un
    # mecanismo aparte -- impersonacion explicita, con registro y con aviso al
    # cliente -- no un permiso permanente.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
