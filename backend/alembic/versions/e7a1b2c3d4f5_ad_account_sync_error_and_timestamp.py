"""Add last_sync_error_code, last_sync_error_detail and last_synced_at to ad_accounts

Revision ID: e7a1b2c3d4f5
Revises: d1a2b3c4d5f9
Create Date: 2026-08-18 21:50:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a1b2c3d4f5'
down_revision: Union[str, Sequence[str], None] = 'd1a2b3c4d5f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ad_accounts', sa.Column('last_sync_error_code', sa.Integer(), nullable=True))
    op.add_column('ad_accounts', sa.Column('last_sync_error_detail', sa.String(), nullable=True))
    op.add_column('ad_accounts', sa.Column('last_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ad_accounts', 'last_synced_at')
    op.drop_column('ad_accounts', 'last_sync_error_detail')
    op.drop_column('ad_accounts', 'last_sync_error_code')
