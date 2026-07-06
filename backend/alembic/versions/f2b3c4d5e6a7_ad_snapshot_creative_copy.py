"""Add creative_title/creative_body/creative_cta_type to ad_snapshots
(captura el copy real del anuncio ganador para inspirar al LLM en la Fase 3B)

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-07-06 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b3c4d5e6a7'
down_revision: Union[str, Sequence[str], None] = 'e1a2b3c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ad_snapshots', sa.Column('creative_title', sa.String(), nullable=True))
    op.add_column('ad_snapshots', sa.Column('creative_body', sa.Text(), nullable=True))
    op.add_column('ad_snapshots', sa.Column('creative_cta_type', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ad_snapshots', 'creative_cta_type')
    op.drop_column('ad_snapshots', 'creative_body')
    op.drop_column('ad_snapshots', 'creative_title')
