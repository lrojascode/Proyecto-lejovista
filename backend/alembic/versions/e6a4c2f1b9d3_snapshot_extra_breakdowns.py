"""Add extra_breakdowns (gender/device/region) to campaign_snapshots — P-2

Revision ID: e6a4c2f1b9d3
Revises: d5f2b1c8a3e4
Create Date: 2026-07-03 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6a4c2f1b9d3'
down_revision: Union[str, Sequence[str], None] = 'd5f2b1c8a3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('campaign_snapshots', sa.Column('extra_breakdowns', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('campaign_snapshots', 'extra_breakdowns')
