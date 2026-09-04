"""spec 03: versiones de propuesta, autorizaciones e intenciones de escritura

Tres tablas nuevas (docs/specs/03-idempotencia-y-reconciliacion-de-escrituras-meta.md):

* `campaign_proposal_versions` — la fotografia inmutable que se aprueba. Hasta
  ahora la aprobacion no estaba atada a nada: se podia aprobar una propuesta,
  modificarla y ejecutar una version distinta de la autorizada.
* `campaign_approvals` — autorizacion ligada a UNA version y consumible una sola
  vez. La restriccion unica por version impide que aprobar dos veces produzca dos
  autorizaciones consumibles.
* `meta_write_intents` — el registro que precede a cada escritura real hacia
  Meta, con su clave logica de idempotencia y el identificador definitivo que
  devolvio Meta.

Es una migracion puramente ADITIVA: no toca ninguna tabla existente, asi que la
imagen anterior funciona sobre este esquema sin cambios. Las Sagas ya en vuelo
cuando se despliegue no llevan `approval_id` en su contexto y siguen el camino
anterior (sin registro de intencion), que es preferible a romperlas a mitad.

Revision ID: s03c7f2a5d8e4
Revises: s02b5e8a1c4f7
"""
import sqlalchemy as sa
from alembic import op

revision = "s03c7f2a5d8e4"
down_revision = "s02b5e8a1c4f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_proposal_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("draft_id", sa.String(), sa.ForeignKey("campaign_drafts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("ad_account_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("material_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("gates_json", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("change_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("draft_id", "version_number", name="uq_proposal_version_number"),
    )
    op.create_index("ix_cpv_draft_id", "campaign_proposal_versions", ["draft_id"])
    op.create_index("ix_cpv_organization_id", "campaign_proposal_versions", ["organization_id"])
    op.create_index("ix_cpv_ad_account_id", "campaign_proposal_versions", ["ad_account_id"])
    op.create_index("ix_cpv_fingerprint", "campaign_proposal_versions", ["material_fingerprint"])

    op.create_table(
        "campaign_approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("version_id", sa.String(), sa.ForeignKey("campaign_proposal_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("approved_by_user_id", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("version_id", name="uq_approval_por_version"),
    )
    op.create_index("ix_ca_version_id", "campaign_approvals", ["version_id"])
    op.create_index("ix_ca_draft_id", "campaign_approvals", ["draft_id"])
    op.create_index("ix_ca_organization_id", "campaign_approvals", ["organization_id"])
    op.create_index("ix_ca_status", "campaign_approvals", ["status"])

    op.create_table(
        "meta_write_intents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("ad_account_id", sa.String(), nullable=False),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("approval_id", sa.String(), nullable=True),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("correlation_tag", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mwi_key", "meta_write_intents", ["idempotency_key"], unique=True)
    op.create_index("ix_mwi_organization_id", "meta_write_intents", ["organization_id"])
    op.create_index("ix_mwi_ad_account_id", "meta_write_intents", ["ad_account_id"])
    op.create_index("ix_mwi_draft_id", "meta_write_intents", ["draft_id"])
    op.create_index("ix_mwi_approval_id", "meta_write_intents", ["approval_id"])
    op.create_index("ix_mwi_operation", "meta_write_intents", ["operation"])
    op.create_index("ix_mwi_status", "meta_write_intents", ["status"])
    op.create_index("ix_mwi_external_id", "meta_write_intents", ["external_id"])


def downgrade() -> None:
    op.drop_table("meta_write_intents")
    op.drop_table("campaign_approvals")
    op.drop_table("campaign_proposal_versions")
