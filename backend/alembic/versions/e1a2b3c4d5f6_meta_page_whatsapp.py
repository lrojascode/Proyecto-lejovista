"""Add whatsapp_number column to meta_pages (Fase 4: descubrimiento de página + WhatsApp)

Revision ID: e1a2b3c4d5f6
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a2b3c4d5f6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'meta_pages',
        sa.Column('whatsapp_number', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meta_pages', 'whatsapp_number')
