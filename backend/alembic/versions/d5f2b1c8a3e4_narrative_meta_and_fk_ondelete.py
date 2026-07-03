"""Narrative metadata columns + FK ondelete homogenization

Revision ID: d5f2b1c8a3e4
Revises: c3d1e5a9f7b2
Create Date: 2026-07-03 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f2b1c8a3e4'
down_revision: Union[str, Sequence[str], None] = 'c3d1e5a9f7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (constraint_name, table, referred_table, local_col, remote_col, ondelete)
_FKS = [
    ("ad_accounts_organization_id_fkey", "ad_accounts", "organizations", "organization_id", "id", "CASCADE"),
    ("campaign_snapshots_ad_account_id_fkey", "campaign_snapshots", "ad_accounts", "ad_account_id", "id", "CASCADE"),
    ("campaign_snapshots_organization_id_fkey", "campaign_snapshots", "organizations", "organization_id", "id", "CASCADE"),
    ("ai_insights_ad_account_id_fkey", "ai_insights", "ad_accounts", "ad_account_id", "id", "CASCADE"),
    ("ai_insights_organization_id_fkey", "ai_insights", "organizations", "organization_id", "id", "CASCADE"),
    ("ai_actions_insight_id_fkey", "ai_actions", "ai_insights", "insight_id", "id", "SET NULL"),
    ("ai_actions_ad_account_id_fkey", "ai_actions", "ad_accounts", "ad_account_id", "id", "CASCADE"),
    ("ai_actions_organization_id_fkey", "ai_actions", "organizations", "organization_id", "id", "CASCADE"),
    ("sync_jobs_ad_account_id_fkey", "sync_jobs", "ad_accounts", "ad_account_id", "id", "CASCADE"),
    ("sync_jobs_organization_id_fkey", "sync_jobs", "organizations", "organization_id", "id", "CASCADE"),
]


def upgrade() -> None:
    """Upgrade schema."""
    # D-4: metadatos de trazabilidad de la narrativa + tipo Text para el cache.
    op.add_column('ad_accounts', sa.Column('narrative_generated_at', sa.DateTime(), nullable=True))
    op.add_column('ad_accounts', sa.Column('narrative_model', sa.String(), nullable=True))
    op.add_column('ad_accounts', sa.Column('narrative_prompt_version', sa.String(), nullable=True))
    op.alter_column('ad_accounts', 'cached_narrative', type_=sa.Text(), existing_nullable=True)

    # D-6: recrear FKs con ondelete homogéneo a nivel de base de datos.
    for name, table, ref_table, local_col, remote_col, ondelete in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref_table, [local_col], [remote_col], ondelete=ondelete)


def downgrade() -> None:
    """Downgrade schema."""
    for name, table, ref_table, local_col, remote_col, _ in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref_table, [local_col], [remote_col])

    op.alter_column('ad_accounts', 'cached_narrative', type_=sa.String(), existing_nullable=True)
    op.drop_column('ad_accounts', 'narrative_prompt_version')
    op.drop_column('ad_accounts', 'narrative_model')
    op.drop_column('ad_accounts', 'narrative_generated_at')
