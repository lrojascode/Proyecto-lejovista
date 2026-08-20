"""add results to campaign/adset/ad snapshots

`resultados = leads + conversaciones de WhatsApp` (execution/04 §1).

No se puede derivar de las columnas existentes: `conversations` trae los leads
como proxy cuando la campaña no tiene mensajería, así que sumarla con
`conversions` contaría cada lead dos veces. Esta columna se llena en el sync a
partir de los `actions` crudos de Meta.

Nullable: las filas anteriores no lo tienen y no se puede recalcular sin volver
a pedirle los `actions` a Meta. Quien la lea cae al comportamiento anterior.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

_TABLES = ("campaign_snapshots", "adset_snapshots", "ad_snapshots")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("results", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "results")
