"""Dedupe + unique constraint and (ad_account_id, date) index on campaign_snapshots

Revision ID: c3d1e5a9f7b2
Revises: a70ab36472b6
Create Date: 2026-07-03 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3d1e5a9f7b2'
down_revision: Union[str, Sequence[str], None] = 'a70ab36472b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Eliminar duplicados preexistentes conservando el snapshot más reciente
    #    por (ad_account_id, campaign_id, date). Desempate por created_at y luego por id.
    op.execute(
        """
        DELETE FROM campaign_snapshots a
        USING campaign_snapshots b
        WHERE a.ad_account_id = b.ad_account_id
          AND a.campaign_id = b.campaign_id
          AND a.date = b.date
          AND (
                a.created_at < b.created_at
             OR (a.created_at = b.created_at AND a.id < b.id)
          )
        """
    )

    # 2. Constraint de unicidad (idempotencia del sync)
    op.create_unique_constraint(
        "uq_snapshot_acc_campaign_date",
        "campaign_snapshots",
        ["ad_account_id", "campaign_id", "date"],
    )

    # 3. Índice compuesto para la query dominante del AIEngine (ad_account_id + rango de fechas)
    op.create_index(
        "idx_snapshot_acc_date",
        "campaign_snapshots",
        ["ad_account_id", "date"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_snapshot_acc_date", table_name="campaign_snapshots")
    op.drop_constraint("uq_snapshot_acc_campaign_date", "campaign_snapshots", type_="unique")
