"""Add campaign_chat_sessions table (asistente conversacional de creación de campañas)

Revision ID: e5f7a9c1b3d8
Revises: d1e2f3a4b5c6
Create Date: 2026-08-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f7a9c1b3d8'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'campaign_chat_sessions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('created_by_user_id', sa.String(), nullable=True),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('state', sa.String(), nullable=False, server_default='AWAITING_MEDIA'),
        sa.Column('status', sa.String(), nullable=False, server_default='OPEN'),
        sa.Column('slots_json', sa.JSON(), nullable=True),
        sa.Column('proposal_json', sa.JSON(), nullable=True),
        sa.Column('preflight_json', sa.JSON(), nullable=True),
        sa.Column('risk_json', sa.JSON(), nullable=True),
        sa.Column('messages_json', sa.JSON(), nullable=True),
        sa.Column('draft_id', sa.String(), nullable=True),
        sa.Column('turn_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('llm_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_client_turn_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['draft_id'], ['campaign_drafts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('draft_id', name='uq_campaign_chat_sessions_draft_id'),
    )
    op.create_index('ix_campaign_chat_sessions_organization_id', 'campaign_chat_sessions', ['organization_id'])
    op.create_index('ix_campaign_chat_sessions_ad_account_id', 'campaign_chat_sessions', ['ad_account_id'])
    op.create_index('ix_campaign_chat_sessions_state', 'campaign_chat_sessions', ['state'])
    op.create_index('ix_campaign_chat_sessions_status', 'campaign_chat_sessions', ['status'])
    op.create_index('ix_campaign_chat_sessions_created_at', 'campaign_chat_sessions', ['created_at'])
    op.create_index('ix_campaign_chat_sessions_updated_at', 'campaign_chat_sessions', ['updated_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_campaign_chat_sessions_updated_at', table_name='campaign_chat_sessions')
    op.drop_index('ix_campaign_chat_sessions_created_at', table_name='campaign_chat_sessions')
    op.drop_index('ix_campaign_chat_sessions_status', table_name='campaign_chat_sessions')
    op.drop_index('ix_campaign_chat_sessions_state', table_name='campaign_chat_sessions')
    op.drop_index('ix_campaign_chat_sessions_ad_account_id', table_name='campaign_chat_sessions')
    op.drop_index('ix_campaign_chat_sessions_organization_id', table_name='campaign_chat_sessions')
    op.drop_table('campaign_chat_sessions')
