"""Add llm_provider and llm_api_key_encrypted to organizations

Revision ID: d1a2b3c4d5f9
Revises: c1a2b3c4d5f8
Create Date: 2026-08-18 21:20:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1a2b3c4d5f9'
down_revision: Union[str, Sequence[str], None] = 'c1a2b3c4d5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('organizations', sa.Column('llm_provider', sa.String(), nullable=True))
    op.add_column('organizations', sa.Column('llm_api_key_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organizations', 'llm_api_key_encrypted')
    op.drop_column('organizations', 'llm_provider')
