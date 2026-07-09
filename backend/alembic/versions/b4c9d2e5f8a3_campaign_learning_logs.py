"""Add campaign_learning_logs table (historial de entrenamiento de la IA en creación de campañas)

Revision ID: b4c9d2e5f8a3
Revises: a3b8c1d4e7f2
Create Date: 2026-07-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c9d2e5f8a3'
down_revision: Union[str, Sequence[str], None] = 'a3b8c1d4e7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'campaign_learning_logs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=True),
        sa.Column('ad_account_id', sa.String(), nullable=True),
        sa.Column('draft_id', sa.String(), nullable=True),
        sa.Column('product', sa.String(), nullable=True),
        sa.Column('zone', sa.String(), nullable=True),
        sa.Column('params_json', sa.JSON(), nullable=True),
        sa.Column('analysis_json', sa.JSON(), nullable=True),
        sa.Column('maturity_json', sa.JSON(), nullable=True),
        sa.Column('risk_level', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='PROPOSED'),
        sa.Column('outcome_json', sa.JSON(), nullable=True),
        sa.Column('outcome_recorded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ),
        sa.ForeignKeyConstraint(['draft_id'], ['campaign_drafts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_campaign_learning_logs_organization_id', 'campaign_learning_logs', ['organization_id'])
    op.create_index('ix_campaign_learning_logs_ad_account_id', 'campaign_learning_logs', ['ad_account_id'])
    op.create_index('ix_campaign_learning_logs_draft_id', 'campaign_learning_logs', ['draft_id'])
    op.create_index('ix_campaign_learning_logs_status', 'campaign_learning_logs', ['status'])
    op.create_index('ix_campaign_learning_logs_created_at', 'campaign_learning_logs', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_campaign_learning_logs_created_at', table_name='campaign_learning_logs')
    op.drop_index('ix_campaign_learning_logs_status', table_name='campaign_learning_logs')
    op.drop_index('ix_campaign_learning_logs_draft_id', table_name='campaign_learning_logs')
    op.drop_index('ix_campaign_learning_logs_ad_account_id', table_name='campaign_learning_logs')
    op.drop_index('ix_campaign_learning_logs_organization_id', table_name='campaign_learning_logs')
    op.drop_table('campaign_learning_logs')
