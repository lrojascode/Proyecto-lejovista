"""spec13: interruptores operativos, feature flags y bitácora de cambios

Revision ID: s13f6b2d8a4c7
Revises: s12e5a9c3f7b4

Tres tablas, todas vacías al crearse.

**Vacías a propósito.** En `operational_switches` solo existen filas para lo
apagado: la ausencia significa encendido, que es el estado normal. Sembrarla con
una fila por capacidad y por organización obligaría a mantenerla al alta de cada
cliente, y el día que se olvidara ese cliente nacería con todo apagado — el modo
de fallo más caro posible para una tabla que existe para contener incidentes.

Lo mismo con `feature_flags`: un flag sin fila toma el valor predeterminado que
declara `app/services/feature_flags.py`, junto al flag y no en la base, para que
quien lee el código sepa qué pasa con quien todavía no lo tiene.

`operational_changes` guarda la historia. Va aparte de las otras dos porque
esas guardan el estado ACTUAL: sin la bitácora, "¿desde cuándo está apagado
esto?" solo se contesta con un `updated_at` que se pisa en cada cambio.
"""
import sqlalchemy as sa
from alembic import op

revision = "s13f6b2d8a4c7"
down_revision = "s12e5a9c3f7b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_switches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("capacidad", sa.String(), nullable=False),
        # NULL = global. La resolución mira primero la fila de la Organización,
        # así que apagar una capacidad para un cliente no toca a los demás.
        sa.Column("organization_id", sa.String(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("encendido", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("actualizado_por_user_id", sa.String(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actualizado_por_email", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("capacidad", "organization_id", name="uq_switch_capacidad_org"),
    )
    for col in ("capacidad", "organization_id", "updated_at"):
        op.create_index(f"ix_operational_switches_{col}", "operational_switches", [col])

    op.create_table(
        "feature_flags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("clave", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        # La cuenta piloto: el ámbito más estrecho. Probar con una cuenta y no
        # con una organización entera es la diferencia entre un error acotado y
        # un cliente enfadado.
        sa.Column("ad_account_id", sa.String(),
                  sa.ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column("actualizado_por_user_id", sa.String(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("clave", "organization_id", "ad_account_id",
                            name="uq_flag_clave_ambito"),
    )
    for col in ("clave", "organization_id", "ad_account_id"):
        op.create_index(f"ix_feature_flags_{col}", "feature_flags", [col])

    op.create_table(
        "operational_changes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tipo", sa.String(), nullable=False),
        sa.Column("clave", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("ad_account_id", sa.String(), nullable=True),
        sa.Column("valor_anterior", sa.String(), nullable=True),
        sa.Column("valor_nuevo", sa.String(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    for col in ("tipo", "clave", "organization_id", "created_at"):
        op.create_index(f"ix_operational_changes_{col}", "operational_changes", [col])


def downgrade() -> None:
    op.drop_table("operational_changes")
    op.drop_table("feature_flags")
    op.drop_table("operational_switches")
