"""Registrar el proveedor LLM real de la narrativa (AI-4)

Revision ID: b3e8f4a1c9d7
Revises: f1b7d9c4e2a6
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e8f4a1c9d7'
down_revision: Union[str, Sequence[str], None] = 'f1b7d9c4e2a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # AI-4: narrative_model por sí solo es ambiguo entre proveedores (ej.
    # "gpt-5.4-mini" vía OpenAI directo vs. vía OpenRouter). Se guarda el
    # proveedor real que sirvió la respuesta (openai/gemini_direct/openrouter).
    op.add_column('ad_accounts', sa.Column('narrative_provider', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ad_accounts', 'narrative_provider')
