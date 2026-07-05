"""Tablas de archivo para snapshots viejos (D-3)

Revision ID: c4d9e2b6f3a1
Revises: b3e8f4a1c9d7
Create Date: 2026-07-05 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d9e2b6f3a1'
down_revision: Union[str, Sequence[str], None] = 'b3e8f4a1c9d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # D-3: sin FKs a propósito — el archivo debe sobrevivir aunque se borre el
    # tenant/cuenta (ver app/models/snapshot_archive.py para el razonamiento).
    op.create_table(
        'campaign_snapshots_archive',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('original_id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('campaign_name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('budget', sa.Float(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('reach', sa.Integer(), nullable=False),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.Column('conversions', sa.Integer(), nullable=False),
        sa.Column('conversations', sa.Integer(), nullable=False),
        sa.Column('cpl', sa.Float(), nullable=False),
        sa.Column('ctr', sa.Float(), nullable=False),
        sa.Column('frequency', sa.Float(), nullable=False),
        sa.Column('placements_breakdown', sa.JSON(), nullable=False),
        sa.Column('demographics_breakdown', sa.JSON(), nullable=False),
        sa.Column('extra_breakdowns', sa.JSON(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('archived_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_campaign_snapshot_archive_acc_date', 'campaign_snapshots_archive', ['ad_account_id', 'date'])
    op.create_index(op.f('ix_campaign_snapshots_archive_original_id'), 'campaign_snapshots_archive', ['original_id'])
    op.create_index(op.f('ix_campaign_snapshots_archive_ad_account_id'), 'campaign_snapshots_archive', ['ad_account_id'])
    op.create_index(op.f('ix_campaign_snapshots_archive_organization_id'), 'campaign_snapshots_archive', ['organization_id'])
    op.create_index(op.f('ix_campaign_snapshots_archive_date'), 'campaign_snapshots_archive', ['date'])

    op.create_table(
        'adset_snapshots_archive',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('original_id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('adset_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False),
        sa.Column('reach', sa.Integer(), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.Column('conversions', sa.Integer(), nullable=False),
        sa.Column('conversations', sa.Integer(), nullable=False),
        sa.Column('cpl', sa.Float(), nullable=False),
        sa.Column('ctr', sa.Float(), nullable=False),
        sa.Column('targeting_summary', sa.JSON(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('archived_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_adset_snapshot_archive_acc_date', 'adset_snapshots_archive', ['ad_account_id', 'date'])
    op.create_index(op.f('ix_adset_snapshots_archive_original_id'), 'adset_snapshots_archive', ['original_id'])
    op.create_index(op.f('ix_adset_snapshots_archive_ad_account_id'), 'adset_snapshots_archive', ['ad_account_id'])
    op.create_index(op.f('ix_adset_snapshots_archive_organization_id'), 'adset_snapshots_archive', ['organization_id'])
    op.create_index(op.f('ix_adset_snapshots_archive_date'), 'adset_snapshots_archive', ['date'])

    op.create_table(
        'ad_snapshots_archive',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('original_id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('adset_id', sa.String(), nullable=False),
        sa.Column('ad_id', sa.String(), nullable=False),
        sa.Column('creative_id', sa.String(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False),
        sa.Column('reach', sa.Integer(), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.Column('conversions', sa.Integer(), nullable=False),
        sa.Column('conversations', sa.Integer(), nullable=False),
        sa.Column('cpl', sa.Float(), nullable=False),
        sa.Column('ctr', sa.Float(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('archived_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_ad_snapshot_archive_acc_date', 'ad_snapshots_archive', ['ad_account_id', 'date'])
    op.create_index(op.f('ix_ad_snapshots_archive_original_id'), 'ad_snapshots_archive', ['original_id'])
    op.create_index(op.f('ix_ad_snapshots_archive_ad_account_id'), 'ad_snapshots_archive', ['ad_account_id'])
    op.create_index(op.f('ix_ad_snapshots_archive_organization_id'), 'ad_snapshots_archive', ['organization_id'])
    op.create_index(op.f('ix_ad_snapshots_archive_date'), 'ad_snapshots_archive', ['date'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('ad_snapshots_archive')
    op.drop_table('adset_snapshots_archive')
    op.drop_table('campaign_snapshots_archive')
