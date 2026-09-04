"""spec 07: los medios pasan a ser una entidad con dueno, cuota y caducidad

Los archivos eran filas en un directorio (`MEDIA_ROOT/{org}/{uuid}.jpg`), lo que
dejaba tres huecos:

* **Pertenencia** apoyada en que el id de la organizacion estuviera en la ruta.
  Eso es una convencion, no una comprobacion.
* **Cuota** imposible de aplicar: no habia forma de saber cuanto ocupaba un
  cliente sin recorrer el disco.
* **Caducidad** inexistente: un archivo subido para una propuesta que nunca se
  aprobo permanecia indefinidamente.

Migracion puramente ADITIVA. Los archivos ya subidos NO se registran aqui: sus
rutas siguen resolviendose como antes (la Saga los lee del mismo sitio) y quedan
fuera de la cuota y del ciclo de vida. Inventarles un dueno y una caducidad
retroactivos correria el riesgo de borrar el creativo de una campana viva.

Revision ID: s07f3a8d2c5b6
Revises: s06e2b7c4a9f1
"""
import sqlalchemy as sa
from alembic import op

revision = "s07f3a8d2c5b6"
down_revision = "s06e2b7c4a9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(), nullable=True),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("content_type", sa.String(length=80), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("ancho", sa.Integer(), nullable=True),
        sa.Column("alto", sa.Integer(), nullable=True),
        sa.Column("duracion_segundos", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="TEMPORAL"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_media_organization_id", "media_assets", ["organization_id"])
    op.create_index("ix_media_draft_id", "media_assets", ["draft_id"])
    op.create_index("ix_media_storage_key", "media_assets", ["storage_key"], unique=True)
    op.create_index("ix_media_checksum", "media_assets", ["checksum_sha256"])
    op.create_index("ix_media_status", "media_assets", ["status"])
    op.create_index("ix_media_expires_at", "media_assets", ["expires_at"])
    op.create_index("ix_media_created_at", "media_assets", ["created_at"])


def downgrade() -> None:
    op.drop_table("media_assets")
