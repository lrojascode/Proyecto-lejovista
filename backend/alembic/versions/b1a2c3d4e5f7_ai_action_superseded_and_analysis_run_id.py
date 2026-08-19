"""Add analysis_run_id and superseded_at to ai_actions

Revision ID: b1a2c3d4e5f7
Revises: e5f7a9c1b3d8
Create Date: 2026-08-18 21:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1a2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'e5f7a9c1b3d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_actions', sa.Column('analysis_run_id', sa.String(), nullable=True))
    op.add_column('ai_actions', sa.Column('superseded_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_actions', 'superseded_at')
    op.drop_column('ai_actions', 'analysis_run_id')
