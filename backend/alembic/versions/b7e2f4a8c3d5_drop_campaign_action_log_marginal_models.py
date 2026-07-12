"""Drop campaign_metrics_history, campaigns, action_logs (M-3): modelos con
uso marginal frente al esquema de snapshots real (`campaign_snapshots`) y al
servicio de auditoría muerto (`audit_log_service.py`, eliminado junto con
`ActionLog` en el mismo cambio) — ningún router/servicio/worker los lee ni
escribe.

Revision ID: b7e2f4a8c3d5
Revises: a4f8c2d6e9b1
Create Date: 2026-07-12 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7e2f4a8c3d5'
down_revision: Union[str, Sequence[str], None] = 'a4f8c2d6e9b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # campaign_metrics_history referencia campaigns — se borra primero.
    op.drop_index(op.f('ix_campaign_metrics_history_date'), table_name='campaign_metrics_history')
    op.drop_index(op.f('ix_campaign_metrics_history_campaign_id'), table_name='campaign_metrics_history')
    op.drop_table('campaign_metrics_history')

    op.drop_index(op.f('ix_campaigns_organization_id'), table_name='campaigns')
    op.drop_index(op.f('ix_campaigns_external_campaign_id'), table_name='campaigns')
    op.drop_table('campaigns')

    op.drop_index(op.f('ix_action_logs_timestamp'), table_name='action_logs')
    op.drop_index(op.f('ix_action_logs_organization_id'), table_name='action_logs')
    op.drop_table('action_logs')


def downgrade() -> None:
    op.create_table(
        'action_logs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('action_type', sa.String(), nullable=False),
        sa.Column('meta_endpoint', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_action_logs_organization_id'), 'action_logs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_action_logs_timestamp'), 'action_logs', ['timestamp'], unique=False)

    op.create_table(
        'campaigns',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=False),
        sa.Column('draft_id', sa.String(), nullable=True),
        sa.Column('external_campaign_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('objective', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['draft_id'], ['campaign_drafts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_campaigns_external_campaign_id'), 'campaigns', ['external_campaign_id'], unique=True)
    op.create_index(op.f('ix_campaigns_organization_id'), 'campaigns', ['organization_id'], unique=False)

    op.create_table(
        'campaign_metrics_history',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.String(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('spend', sa.Float(), nullable=False),
        sa.Column('leads', sa.Integer(), nullable=False),
        sa.Column('cpl', sa.Float(), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('campaign_id', 'date', name='uq_campaign_date'),
    )
    op.create_index(op.f('ix_campaign_metrics_history_campaign_id'), 'campaign_metrics_history', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_campaign_metrics_history_date'), 'campaign_metrics_history', ['date'], unique=False)
