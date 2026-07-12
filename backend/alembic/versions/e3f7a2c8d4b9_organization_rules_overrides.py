"""Add campaign_rules_overrides and ai_thresholds_overrides JSON columns to organizations (M-2)

Revision ID: e3f7a2c8d4b9
Revises: b7e2f4a8c3d5
Create Date: 2026-07-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f7a2c8d4b9'
down_revision: Union[str, Sequence[str], None] = 'b7e2f4a8c3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'organizations',
        sa.Column('campaign_rules_overrides', sa.JSON(), nullable=False, server_default='{}'),
    )
    op.add_column(
        'organizations',
        sa.Column('ai_thresholds_overrides', sa.JSON(), nullable=False, server_default='{}'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organizations', 'ai_thresholds_overrides')
    op.drop_column('organizations', 'campaign_rules_overrides')
