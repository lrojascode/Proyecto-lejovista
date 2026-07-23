"""Add account_learning_snapshots table (aprendizaje longitudinal por sync, Q2)

Revision ID: d1e2f3a4b5c6
Revises: c9d3e7f1a2b4
Create Date: 2026-07-22 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c9d3e7f1a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'account_learning_snapshots',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=True),
        sa.Column('ad_account_id', sa.String(), nullable=True),
        sa.Column('window_days', sa.Integer(), nullable=True, server_default='7'),
        sa.Column('kpis_json', sa.JSON(), nullable=True),
        sa.Column('deltas_json', sa.JSON(), nullable=True),
        sa.Column('top_json', sa.JSON(), nullable=True),
        sa.Column('bottom_json', sa.JSON(), nullable=True),
        sa.Column('segmentation_json', sa.JSON(), nullable=True),
        sa.Column('observations_json', sa.JSON(), nullable=True),
        sa.Column('verdict', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_account_learning_snapshots_organization_id', 'account_learning_snapshots', ['organization_id'])
    op.create_index('ix_account_learning_snapshots_ad_account_id', 'account_learning_snapshots', ['ad_account_id'])
    op.create_index('ix_account_learning_snapshots_verdict', 'account_learning_snapshots', ['verdict'])
    op.create_index('ix_account_learning_snapshots_created_at', 'account_learning_snapshots', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_account_learning_snapshots_created_at', table_name='account_learning_snapshots')
    op.drop_index('ix_account_learning_snapshots_verdict', table_name='account_learning_snapshots')
    op.drop_index('ix_account_learning_snapshots_ad_account_id', table_name='account_learning_snapshots')
    op.drop_index('ix_account_learning_snapshots_organization_id', table_name='account_learning_snapshots')
    op.drop_table('account_learning_snapshots')
