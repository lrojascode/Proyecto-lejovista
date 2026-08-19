"""Create invitations table

Revision ID: e1f2a3b4c5d6
Revises: f8b2c3d4e5f6
Create Date: 2026-08-18 22:25:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'f8b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'invitations',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('organization_id', sa.String(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='OPERATOR'),
        sa.Column('token', sa.String(length=100), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('invited_by_user_id', sa.String(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_invitations_organization_id', 'invitations', ['organization_id'])
    op.create_index('ix_invitations_email', 'invitations', ['email'])
    op.create_index('ix_invitations_token', 'invitations', ['token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_invitations_token', table_name='invitations')
    op.drop_index('ix_invitations_email', table_name='invitations')
    op.drop_index('ix_invitations_organization_id', table_name='invitations')
    op.drop_table('invitations')
