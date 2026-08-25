"""app de Meta propia por credencial

`META_APP_ID` / `META_APP_SECRET` son globales (la app de la agencia) y se usan
para DOS cosas: canjear el token por uno long-lived y preguntar por su caducidad
con `debug_token`. Un token emitido por OTRA app no se puede depurar con esas
credenciales: `debug_token` devuelve error, `inspect_token_expiration` lo lee
como `is_valid: False` y la tarea diaria marca la cuenta EXPIRED y suspende el
sync. Sobre un token perfectamente sano.

Aparece en cuanto un cliente usa su propia app de Meta — el caso de ITS, cuyo
System User vive en su Business Manager y solo puede emitir tokens con la app de
ese negocio.

Va en `meta_credentials` y no en `organizations` porque lo que hay que saber no
es "que app usa este cliente" sino "que app emitio ESTE token": una organizacion
puede alojar la cuenta de un cliente conectada con la app de la agencia y la de
otro con la suya, y con un solo valor por organizacion una de las dos queda mal.

Nullable y sin default: `NULL` significa "la app global", que es lo que aplica a
todas las credenciales que ya existen. No hay backfill que hacer.

Revision ID: a9b8c7d6e5f4
Revises: d0e1f2a3b4c5
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "a9b8c7d6e5f4"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meta_credentials", sa.Column("app_id", sa.String(), nullable=True))
    op.add_column(
        "meta_credentials", sa.Column("app_secret_encrypted", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("meta_credentials", "app_secret_encrypted")
    op.drop_column("meta_credentials", "app_id")
