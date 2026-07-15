"""Add default_page_id to ad_accounts (página predeterminada por cuenta)

Revision ID: f4a8b2c6d1e9
Revises: e3f7a2c8d4b9
Create Date: 2026-07-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a8b2c6d1e9'
down_revision: Union[str, Sequence[str], None] = 'e3f7a2c8d4b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ad_accounts',
        sa.Column('default_page_id', sa.String(), nullable=True),
    )
    op.create_foreign_key(
        'fk_ad_accounts_default_page_id',
        'ad_accounts', 'meta_pages',
        ['default_page_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_ad_accounts_default_page_id', 'ad_accounts', type_='foreignkey')
    op.drop_column('ad_accounts', 'default_page_id')
