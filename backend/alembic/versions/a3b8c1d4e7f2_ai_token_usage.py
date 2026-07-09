"""Add ai_token_usage table (medición de consumo de tokens del LLM por funcionalidad)

Revision ID: a3b8c1d4e7f2
Revises: f2b3c4d5e6a7
Create Date: 2026-07-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b8c1d4e7f2'
down_revision: Union[str, Sequence[str], None] = 'f2b3c4d5e6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ai_token_usage',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=True),
        sa.Column('ad_account_id', sa.String(), nullable=True),
        sa.Column('feature', sa.String(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reasoning_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('used_cot', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('estimated_cost_usd', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_token_usage_organization_id', 'ai_token_usage', ['organization_id'])
    op.create_index('ix_ai_token_usage_ad_account_id', 'ai_token_usage', ['ad_account_id'])
    op.create_index('ix_ai_token_usage_feature', 'ai_token_usage', ['feature'])
    op.create_index('ix_ai_token_usage_created_at', 'ai_token_usage', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ai_token_usage_created_at', table_name='ai_token_usage')
    op.drop_index('ix_ai_token_usage_feature', table_name='ai_token_usage')
    op.drop_index('ix_ai_token_usage_ad_account_id', table_name='ai_token_usage')
    op.drop_index('ix_ai_token_usage_organization_id', table_name='ai_token_usage')
    op.drop_table('ai_token_usage')
