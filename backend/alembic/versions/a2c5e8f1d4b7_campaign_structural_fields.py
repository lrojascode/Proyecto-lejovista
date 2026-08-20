"""Add objective/created_time/start_time/stop_time to campaign_snapshots and
end_time to adset_snapshots

Revision ID: a2c5e8f1d4b7
Revises: b2c3d4e5f6a7
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2c5e8f1d4b7'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('campaign_snapshots', sa.Column('objective', sa.String(), nullable=True))
    op.add_column('campaign_snapshots', sa.Column('created_time', sa.DateTime(), nullable=True))
    op.add_column('campaign_snapshots', sa.Column('start_time', sa.DateTime(), nullable=True))
    op.add_column('campaign_snapshots', sa.Column('stop_time', sa.DateTime(), nullable=True))
    op.add_column('adset_snapshots', sa.Column('end_time', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('adset_snapshots', 'end_time')
    op.drop_column('campaign_snapshots', 'stop_time')
    op.drop_column('campaign_snapshots', 'start_time')
    op.drop_column('campaign_snapshots', 'created_time')
    op.drop_column('campaign_snapshots', 'objective')
