"""add enabled_modules to organizations

Vista recortada del cliente (execution/01 §3). NULL = todos los módulos
habilitados, así ninguna organización existente cambia de comportamiento.

Revision ID: c3d4e5f6a7b8
Revises: a2c5e8f1d4b7
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "a2c5e8f1d4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("enabled_modules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "enabled_modules")
