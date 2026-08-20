"""widen ad_account_spend_monthly with impressions, reach and results

El histórico mensual completo ya se bajaba con date_preset=maximum, pero solo
guardaba `spend`. Una fila por mes sigue siendo barata aun para una cuenta de 5
años, y con este ancho se puede responder de verdad sobre años viejos sin
guardar cada día de cada campaña (execution/03 §2).

Default 0 y no NULL: las filas existentes no tienen el dato, y 0 es lo que ya
significaba "no medido" en el resto de la tabla.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None

_COLUMNS = ("impressions", "reach", "results")


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column(
            "ad_account_spend_monthly",
            sa.Column(col, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for col in _COLUMNS:
        op.drop_column("ad_account_spend_monthly", col)
