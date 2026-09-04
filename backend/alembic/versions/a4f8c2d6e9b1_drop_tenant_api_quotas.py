"""Drop tenant_api_quotas (M-1): tabla huérfana del cliente Meta viejo
(MetaApiClient/RateLimiter/MetaCredentialService), nunca consultada ni
escrita por ningún código vivo — el rate limiting real usa
MetaAPIRateLimiter (Redis), no este modelo.

Revision ID: a4f8c2d6e9b1
Revises: d2f6a8c3b1e7
Create Date: 2026-07-12 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a4f8c2d6e9b1'
down_revision: Union[str, Sequence[str], None] = 'd2f6a8c3b1e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONTRACCION-APROBADA (declarada en la spec 14, sobre una migracion ya
    # aplicada). La imagen anterior mapeaba el modelo pero ningun router,
    # servicio ni worker lo consultaba: el rate limiting real vive en Redis.
    # Borrar la tabla no le quita nada que use, asi que sigue funcionando.
    op.drop_table('tenant_api_quotas')


def downgrade() -> None:
    op.create_table(
        'tenant_api_quotas',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('ad_account_id', sa.String(), nullable=True),
        sa.Column('call_count', sa.Integer(), nullable=False),
        sa.Column('business_use_case_usage', sa.Float(), nullable=False),
        sa.Column('last_updated', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['ad_account_id'], ['ad_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ad_account_id'),
        sa.UniqueConstraint('organization_id'),
    )
