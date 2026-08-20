"""add cached_tokens to ai_token_usage

Input servido desde la cache del proveedor (~10x más barato). Viene DENTRO de
`prompt_tokens`, no aparte, así que se resta para cobrar solo la parte no
cacheada a tarifa plena.

Default 0 y NOT NULL: las filas anteriores no tienen el dato y 0 significa
"todo el input se cobró a tarifa plena", que es exactamente como se calculó su
costo en su momento. No hay que recalcular nada.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_token_usage",
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ai_token_usage", "cached_tokens")
