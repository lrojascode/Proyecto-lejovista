"""add effective_status to campaign/adset/ad snapshots

Estado efectivo de entrega segun Meta (execution/01 §4, execution/04 §2).
`status` dice como quedo configurado el objeto; `effective_status` dice si
esta REALMENTE entregando. Una campana ACTIVE con su conjunto pausado no
entrega nada, y Meta solo lo reporta en este campo.

Nullable: los snapshots ya guardados no lo tienen y no se puede inventar.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

_TABLES = ("campaign_snapshots", "adset_snapshots", "ad_snapshots")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("effective_status", sa.String(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "effective_status")
