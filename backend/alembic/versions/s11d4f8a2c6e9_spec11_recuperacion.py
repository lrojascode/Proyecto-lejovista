"""spec11: trazabilidad de propuesta, motivo de cambio humano y reglas candidatas

Revision ID: s11d4f8a2c6e9
Revises: s10c3e7b9d2a5

Tres cosas:

1. **`optimization_goal`** en los snapshots de campaña y de conjunto, y en sus
   archivos. Es el evento que la campaña contaba como resultado, y la segunda
   condición dura de comparabilidad: dos campañas del mismo objetivo, una
   optimizada a conversaciones y otra a formularios, producen costos que no se
   pueden poner en la misma escala.

   Se añade vacía. Las filas anteriores NO se rellenan: el dato vive en Meta y
   pedirlo para todo el histórico son miles de llamadas. La comparabilidad lo
   DERIVA de qué métrica primaria tuvo la campaña, que es el mismo hecho visto
   por la otra cara, así que el histórico sigue siendo comparable desde el
   primer día. El sync lo va rellenando con lo que Meta devuelva.

   Va también en el ARCHIVO, y eso no es simetría por simetría: sin la columna,
   una campaña archivada dejaría de poder ser evidencia comparable — que es
   exactamente la consecuencia de ADR-01 que la spec 06 corrigió.

2. **`proposal_evidence`** — en qué se basó cada propuesta, fijado al proponer.

3. **`proposal_changes`** y **`candidate_rules`** — la corrección humana con su
   motivo, y los patrones repetidos propuestos como regla.
"""
import sqlalchemy as sa
from alembic import op

revision = "s11d4f8a2c6e9"
down_revision = "s10c3e7b9d2a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for tabla in (
        "campaign_snapshots", "adset_snapshots",
        "campaign_snapshots_archive", "adset_snapshots_archive",
    ):
        op.add_column(tabla, sa.Column("optimization_goal", sa.String(), nullable=True))

    op.create_table(
        "proposal_evidence",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(), sa.ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("version_id", sa.String(), nullable=True),
        sa.Column("campanas_fuente", sa.JSON(), nullable=True),
        sa.Column("baseline_json", sa.JSON(), nullable=True),
        sa.Column("confianza_json", sa.JSON(), nullable=True),
        sa.Column("afirmaciones_json", sa.JSON(), nullable=True),
        sa.Column("excluidas_json", sa.JSON(), nullable=True),
        sa.Column("ventana_dias", sa.Integer(), nullable=True),
        sa.Column("campanas_comparables", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nivel_confianza", sa.String(), nullable=False),
        sa.Column("exploratoria", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reglas_version", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("modelo", sa.String(), nullable=True),
        sa.Column("proveedor", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for col in ("organization_id", "ad_account_id", "draft_id", "version_id",
                "nivel_confianza", "exploratoria", "created_at"):
        op.create_index(f"ix_proposal_evidence_{col}", "proposal_evidence", [col])

    op.create_table(
        "proposal_changes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(), sa.ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("version_anterior_id", sa.String(), nullable=True),
        sa.Column("version_nueva_id", sa.String(), nullable=True),
        # NOT NULL a propósito: es la garantía del criterio 11 en el esquema, no
        # solo en la validación. Una ruta que se saltara el servicio seguiría sin
        # poder guardar un cambio sin motivo.
        sa.Column("motivo", sa.String(), nullable=False),
        sa.Column("motivo_detalle", sa.Text(), nullable=True),
        sa.Column("cambios_json", sa.JSON(), nullable=True),
        sa.Column("actor_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for col in ("organization_id", "ad_account_id", "draft_id", "version_anterior_id",
                "version_nueva_id", "motivo", "actor_user_id", "created_at"):
        op.create_index(f"ix_proposal_changes_{col}", "proposal_changes", [col])

    op.create_table(
        "candidate_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("ad_account_id", sa.String(), sa.ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("motivo", sa.String(), nullable=False),
        sa.Column("propuesta", sa.Text(), nullable=False),
        sa.Column("ocurrencias_json", sa.JSON(), nullable=True),
        sa.Column("ocurrencias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="propuesta"),
        sa.Column("resuelta_por_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resuelta_en", sa.DateTime(), nullable=True),
        sa.Column("nota_de_resolucion", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for col in ("organization_id", "ad_account_id", "motivo", "status", "created_at"):
        op.create_index(f"ix_candidate_rules_{col}", "candidate_rules", [col])


def downgrade() -> None:
    op.drop_table("candidate_rules")
    op.drop_table("proposal_changes")
    op.drop_table("proposal_evidence")
    for tabla in (
        "adset_snapshots_archive", "campaign_snapshots_archive",
        "adset_snapshots", "campaign_snapshots",
    ):
        op.drop_column(tabla, "optimization_goal")
