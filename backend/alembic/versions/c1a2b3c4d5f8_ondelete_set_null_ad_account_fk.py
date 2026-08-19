"""Set ondelete SET NULL on ad_account_id FKs in 4 tables

Revision ID: c1a2b3c4d5f8
Revises: b1a2c3d4e5f7
Create Date: 2026-08-18 21:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3c4d5f8'
down_revision: Union[str, Sequence[str], None] = 'b1a2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FKS = [
    ("api_call_logs_ad_account_id_fkey", "api_call_logs", "ad_accounts", "ad_account_id", "id", "SET NULL"),
    ("ai_token_usage_ad_account_id_fkey", "ai_token_usage", "ad_accounts", "ad_account_id", "id", "SET NULL"),
    ("account_learning_snapshots_ad_account_id_fkey", "account_learning_snapshots", "ad_accounts", "ad_account_id", "id", "SET NULL"),
    ("campaign_learning_logs_ad_account_id_fkey", "campaign_learning_logs", "ad_accounts", "ad_account_id", "id", "SET NULL"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for name, table, ref_table, local_col, remote_col, ondelete in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref_table, [local_col], [remote_col], ondelete=ondelete)


def downgrade() -> None:
    """Downgrade schema."""
    for name, table, ref_table, local_col, remote_col, _ in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, ref_table, [local_col], [remote_col])
