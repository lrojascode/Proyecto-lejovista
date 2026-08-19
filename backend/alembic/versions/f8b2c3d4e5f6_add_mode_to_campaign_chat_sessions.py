"""Add mode column to campaign_chat_sessions

Revision ID: f8b2c3d4e5f6
Revises: e7a1b2c3d4f5
Create Date: 2026-08-18 22:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8b2c3d4e5f6'
down_revision: Union[str, None] = 'e7a1b2c3d4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'campaign_chat_sessions',
        sa.Column('mode', sa.String(length=20), nullable=False, server_default='BUILD')
    )


def downgrade() -> None:
    op.drop_column('campaign_chat_sessions', 'mode')
