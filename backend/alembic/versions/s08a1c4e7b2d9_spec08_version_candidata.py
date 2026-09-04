"""spec 08: version candidata de la API de Meta por organizacion

`organizations.meta_api_version_candidata` permite activar una version nueva de
la Graph/Marketing API SOLO en la cuenta piloto, mientras el resto sigue en la
vigente. El rollback es vaciar el campo — no construir ni publicar una imagen.

Vacio = la version configurada globalmente, que es el comportamiento de todas
las organizaciones existentes.

Revision ID: s08a1c4e7b2d9
Revises: s07f3a8d2c5b6
"""
import sqlalchemy as sa
from alembic import op

revision = "s08a1c4e7b2d9"
down_revision = "s07f3a8d2c5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("meta_api_version_candidata", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "meta_api_version_candidata")
