"""Qué cuenta publicitaria puede promocionar qué página.

`MetaPage` es por ORGANIZACIÓN, y eso alcanzaba mientras una organización fuera
un solo cliente. En una que aloja las cuentas de varios, el dropdown de página
de "Crear campaña" mezclaba las páginas de todos — el mismo leak que
`user_ad_accounts` cerró para las cuentas, una tabla más abajo.

**Muchos a muchos y no un FK en `meta_pages`.** Una página puede ser
promocionable desde más de una cuenta del mismo cliente, y `_sync_real_pages`
descubre un conjunto POR CUENTA: con un solo `ad_account_id` en la página, cada
sync le pisaría el dueño al anterior y la página iría rebotando entre cuentas.

El enlace lo mantiene el sync, que además **poda** los que ya no corresponden:
sin eso, el seed de la migración —deliberadamente amplio, para no dejar a nadie
sin páginas al desplegar— se quedaría amplio para siempre. Es la lección de
44f4ec2: un leak que sobrevive a su propio arreglo porque el guardado era puro
upsert y nadie reconciliaba.
"""
from datetime import datetime
import uuid
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MetaPageAdAccount(Base):
    __tablename__ = "meta_page_ad_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    meta_page_id: Mapped[str] = mapped_column(
        String, ForeignKey("meta_pages.id", ondelete="CASCADE"), index=True
    )
    ad_account_id: Mapped[str] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meta_page = relationship("MetaPage")
    ad_account = relationship("AdAccount")

    __table_args__ = (
        UniqueConstraint("meta_page_id", "ad_account_id", name="uq_meta_page_ad_account"),
    )
