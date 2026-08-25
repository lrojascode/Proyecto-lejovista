"""permisos por cuenta publicitaria dentro de la organizacion

Hasta ahora la organizacion era la UNICA unidad de aislamiento: ser miembro era
todo o nada. Al invitar al usuario de un cliente a una organizacion que aloja
las cuentas de varios clientes, ese usuario veia todas.

Semantica fail-closed: sin filas, un usuario no ve ninguna cuenta. Para que el
despliegue no le quite acceso a nadie, esta migracion SIEMBRA una fila por cada
(miembro x cuenta de su organizacion) que exista hoy. A partir de ahi, quitarle
a un cliente las cuentas ajenas es borrar filas: un acto explicito, no un
silencio. Lo contrario -"sin filas = ve todo"- haria que el leak sobreviviera a
su propio arreglo, que es lo que paso con las paginas (44f4ec2).

El seed va en SQL y no en ORM a proposito: una migracion no debe depender de la
forma que tengan los modelos en el futuro.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ad_accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ad_account_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ad_account_id"], ["ad_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ad_account_id", name="uq_user_ad_account"),
    )
    op.create_index("ix_user_ad_accounts_user_id", "user_ad_accounts", ["user_id"])
    op.create_index("ix_user_ad_accounts_ad_account_id", "user_ad_accounts", ["ad_account_id"])
    op.create_index("ix_user_ad_accounts_organization_id", "user_ad_accounts", ["organization_id"])

    # Seed retrocompatible: cada miembro conserva lo que ya veia.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        nuevo_id = "gen_random_uuid()::text"
    else:
        # SQLite (tests). lower(hex(randomblob(16))) alcanza como identificador
        # opaco; no tiene que ser un UUID canonico.
        nuevo_id = "lower(hex(randomblob(16)))"

    op.execute(sa.text(f"""
        INSERT INTO user_ad_accounts (id, user_id, ad_account_id, organization_id, created_at)
        SELECT {nuevo_id}, ou.user_id, aa.id, aa.organization_id, CURRENT_TIMESTAMP
        FROM organization_users ou
        JOIN ad_accounts aa ON aa.organization_id = ou.organization_id
    """))

    # Que cuentas se le conceden al invitado cuando acepta. Sin esto, un
    # invitado nuevo entra y no ve nada hasta que un ADMIN se acuerde.
    op.add_column("invitations", sa.Column("ad_account_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("invitations", "ad_account_ids")
    op.drop_index("ix_user_ad_accounts_organization_id", table_name="user_ad_accounts")
    op.drop_index("ix_user_ad_accounts_ad_account_id", table_name="user_ad_accounts")
    op.drop_index("ix_user_ad_accounts_user_id", table_name="user_ad_accounts")
    op.drop_table("user_ad_accounts")
