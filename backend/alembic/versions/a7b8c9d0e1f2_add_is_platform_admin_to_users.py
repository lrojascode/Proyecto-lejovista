"""add is_platform_admin to users

Administrador de plataforma (execution/02 §1). No es un rol de organizacion:
los tres roles de OrganizationUser son siempre DENTRO de una organizacion.

Este flag permite crear organizaciones, invitar a su primer ADMIN y configurar
modulos. NO da acceso a los datos de ningun tenant.

Default false y NOT NULL: nadie es admin de plataforma hasta que se lo marque
explicitamente en la base.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_platform_admin")
