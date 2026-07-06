"""Add budget column to adset_snapshots (Bug 5: presupuesto ABO a nivel adset)

Revision ID: a1b2c3d4e5f6
Revises: c4d9e2b6f3a1
Create Date: 2026-07-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c4d9e2b6f3a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'adset_snapshots',
        sa.Column('budget', sa.Float(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('adset_snapshots', 'budget')
