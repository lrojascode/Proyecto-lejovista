"""Add industry column to organizations (rubro del negocio, para políticas de Meta)

Revision ID: d2f6a8c3b1e7
Revises: c8d3e5f1a9b4
Create Date: 2026-07-09 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f6a8c3b1e7'
down_revision: Union[str, Sequence[str], None] = 'c8d3e5f1a9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('organizations', sa.Column('industry', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organizations', 'industry')
