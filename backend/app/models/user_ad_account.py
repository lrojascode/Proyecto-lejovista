"""Qué cuentas publicitarias puede ver cada usuario DENTRO de su organización.

Hasta ahora el aislamiento tenía una sola unidad: la organización. Ser miembro
era todo o nada, así que al invitar al usuario de un cliente a una organización
que aloja las cuentas de varios clientes, ese usuario veía todas — el leak que
motivó esta tabla.

Semántica: **fail-closed**. Un usuario ve exactamente las cuentas que tiene
concedidas; sin filas, no ve ninguna. La migración que crea la tabla siembra una
fila por cada (miembro × cuenta de su organización) que exista, para que el
despliegue no le quite acceso a nadie: quitarle a un cliente las cuentas ajenas
pasa a ser borrar filas, un acto explícito. Lo contrario —"sin filas = ve todo"—
haría que el leak sobreviviera a su propio arreglo, que es exactamente lo que
pasó con las páginas (44f4ec2: se acotó el descubrimiento y las ya guardadas se
quedaron para siempre).

`organization_id` está desnormalizado a propósito: la concesión solo tiene
sentido dentro de una organización, y tenerlo acá permite limpiar de una las
concesiones de un tenant sin pasar por `ad_accounts`.
"""
from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class UserAdAccount(Base):
    __tablename__ = "user_ad_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ad_account_id: Mapped[str] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User")
    ad_account = relationship("AdAccount")

    __table_args__ = (
        UniqueConstraint("user_id", "ad_account_id", name="uq_user_ad_account"),
    )
