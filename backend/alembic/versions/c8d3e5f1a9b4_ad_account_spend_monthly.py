"""Add ad_account_spend_monthly table (histórico de inversión mensual por cuenta)

Revision ID: c8d3e5f1a9b4
Revises: b4c9d2e5f8a3
Create Date: 2026-07-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d3e5f1a9b4'
down_revision: Union[str, Sequence[str], None] = 'b4c9d2e5f8a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ad_account_spend_monthly',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('year_month', sa.String(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False, server_default='0'),
        sa.Column('closed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ad_account_id', 'year_month', name='uq_ad_account_year_month'),
    )
    op.create_index('ix_ad_account_spend_monthly_ad_account_id', 'ad_account_spend_monthly', ['ad_account_id'])
    op.create_index('ix_ad_account_spend_monthly_organization_id', 'ad_account_spend_monthly', ['organization_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ad_account_spend_monthly_organization_id', table_name='ad_account_spend_monthly')
    op.drop_index('ix_ad_account_spend_monthly_ad_account_id', table_name='ad_account_spend_monthly')
    op.drop_table('ad_account_spend_monthly')
