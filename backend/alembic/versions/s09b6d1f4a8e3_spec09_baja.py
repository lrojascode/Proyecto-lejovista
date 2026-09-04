"""spec 09: estado de la baja de una organizacion

`organizations.offboarding_state` registra la fase de la baja, sus plazos, quien
la inicio y el detalle de lo eliminado. Vacio = organizacion activa.

La fila de `organizations` NO se borra al completar una baja: se conserva vacia
como constancia de que existio y de cuando se dio de baja. Un DELETE ahi cascadea
a media base y deja la baja sin rastro auditable, que es justo lo contrario de lo
que un certificado de eliminacion necesita.

Revision ID: s09b6d1f4a8e3
Revises: s08a1c4e7b2d9
"""
import sqlalchemy as sa
from alembic import op

revision = "s09b6d1f4a8e3"
down_revision = "s08a1c4e7b2d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("offboarding_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "offboarding_state")
