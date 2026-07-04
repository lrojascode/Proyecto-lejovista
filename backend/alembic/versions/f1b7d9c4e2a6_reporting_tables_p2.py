"""P-2.1: reach/conversations + adset/ad/creative/audience tables

Revision ID: f1b7d9c4e2a6
Revises: e6a4c2f1b9d3
Create Date: 2026-07-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b7d9c4e2a6'
down_revision: Union[str, Sequence[str], None] = 'e6a4c2f1b9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- campaign_snapshots: reach + conversations ---
    op.add_column('campaign_snapshots', sa.Column('reach', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('campaign_snapshots', sa.Column('conversations', sa.Integer(), nullable=False, server_default='0'))

    # --- adset_snapshots ---
    op.create_table(
        'adset_snapshots',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('adset_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False, server_default='0'),
        sa.Column('reach', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('impressions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clicks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversations', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cpl', sa.Float(), nullable=False, server_default='0'),
        sa.Column('ctr', sa.Float(), nullable=False, server_default='0'),
        sa.Column('targeting_summary', sa.JSON(), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ad_account_id', 'adset_id', 'date', name='uq_adset_snapshot_acc_adset_date'),
    )
    op.create_index('idx_adset_snapshot_acc_date', 'adset_snapshots', ['ad_account_id', 'date'])
    op.create_index(op.f('ix_adset_snapshots_campaign_id'), 'adset_snapshots', ['campaign_id'])
    op.create_index(op.f('ix_adset_snapshots_adset_id'), 'adset_snapshots', ['adset_id'])
    op.create_index(op.f('ix_adset_snapshots_date'), 'adset_snapshots', ['date'])

    # --- ad_snapshots ---
    op.create_table(
        'ad_snapshots',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('adset_id', sa.String(), nullable=False),
        sa.Column('ad_id', sa.String(), nullable=False),
        sa.Column('creative_id', sa.String(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False, server_default='0'),
        sa.Column('reach', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('impressions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clicks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversations', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cpl', sa.Float(), nullable=False, server_default='0'),
        sa.Column('ctr', sa.Float(), nullable=False, server_default='0'),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ad_account_id', 'ad_id', 'date', name='uq_ad_snapshot_acc_ad_date'),
    )
    op.create_index('idx_ad_snapshot_acc_date', 'ad_snapshots', ['ad_account_id', 'date'])
    op.create_index(op.f('ix_ad_snapshots_campaign_id'), 'ad_snapshots', ['campaign_id'])
    op.create_index(op.f('ix_ad_snapshots_adset_id'), 'ad_snapshots', ['adset_id'])
    op.create_index(op.f('ix_ad_snapshots_ad_id'), 'ad_snapshots', ['ad_id'])
    op.create_index(op.f('ix_ad_snapshots_date'), 'ad_snapshots', ['date'])

    # --- meta_creatives ---
    op.create_table(
        'meta_creatives',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('image_hash', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('meta_created_time', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ad_account_id', 'image_hash', name='uq_creative_acc_hash'),
    )
    op.create_index(op.f('ix_meta_creatives_image_hash'), 'meta_creatives', ['image_hash'])

    # --- custom_audiences ---
    op.create_table(
        'custom_audiences',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('subtype', sa.String(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('approximate_count', sa.Integer(), nullable=True),
        sa.Column('meta_time_created', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ad_account_id', 'external_id', name='uq_audience_acc_external'),
    )
    op.create_index(op.f('ix_custom_audiences_external_id'), 'custom_audiences', ['external_id'])


def downgrade() -> None:
    op.drop_table('custom_audiences')
    op.drop_table('meta_creatives')
    op.drop_table('ad_snapshots')
    op.drop_table('adset_snapshots')
    op.drop_column('campaign_snapshots', 'conversations')
    op.drop_column('campaign_snapshots', 'reach')
