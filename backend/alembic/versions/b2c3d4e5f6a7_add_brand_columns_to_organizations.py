"""Add brand columns to organizations

Revision ID: b2c3d4e5f6a7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-18 22:32:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column('display_name', sa.String(), nullable=True))
    op.add_column('organizations', sa.Column('logo_url', sa.String(), nullable=True))
    op.add_column('organizations', sa.Column('accent_color', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('organizations', 'accent_color')
    op.drop_column('organizations', 'logo_url')
    op.drop_column('organizations', 'display_name')
