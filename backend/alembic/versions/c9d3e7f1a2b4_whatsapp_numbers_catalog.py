"""Catálogo de números de WhatsApp por organización (F1)

Hasta ahora cada página guardaba UN número (`meta_pages.whatsapp_number`) y la
UI ni siquiera dejaba corregirlo una vez guardado. Esta tabla permite tener
varios por organización, editarlos y elegir cuál usa cada campaña.

`meta_pages.whatsapp_number` se conserva: sigue siendo el número por defecto de
la página y lo usan la Saga y el executor.

Revision ID: c9d3e7f1a2b4
Revises: f4a8b2c6d1e9
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d3e7f1a2b4"
down_revision = "f4a8b2c6d1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_numbers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("display_number", sa.String(), nullable=False),
        # META = descubierto vía WhatsApp Business API (lo reescribe el sync);
        # MANUAL = cargado por el usuario.
        sa.Column("source", sa.String(), nullable=False, server_default="MANUAL"),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("verified_name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "display_number", name="uq_org_whatsapp_number"),
        sa.UniqueConstraint("organization_id", "external_id", name="uq_org_whatsapp_external"),
    )
    op.create_index(
        op.f("ix_whatsapp_numbers_organization_id"),
        "whatsapp_numbers", ["organization_id"], unique=False,
    )

    # Sembrar el catálogo con los números que ya están cargados en las páginas,
    # para que la lista no aparezca vacía tras desplegar. DISTINCT porque dos
    # páginas de la misma organización pueden compartir número y la unicidad
    # por (organization_id, display_number) lo rechazaría.
    op.execute(
        """
        INSERT INTO whatsapp_numbers (id, organization_id, display_number, source, status, created_at)
        SELECT DISTINCT ON (organization_id, whatsapp_number)
               gen_random_uuid()::text, organization_id, whatsapp_number,
               'MANUAL', 'ACTIVE', NOW()
        FROM meta_pages
        WHERE whatsapp_number IS NOT NULL AND whatsapp_number <> ''
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_whatsapp_numbers_organization_id"), table_name="whatsapp_numbers")
    op.drop_table("whatsapp_numbers")
