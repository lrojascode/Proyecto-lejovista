"""paginas y numeros de whatsapp acotados por cuenta publicitaria

`meta_pages` y `whatsapp_numbers` son por ORGANIZACION. Eso alcanzaba mientras
una organizacion fuera un solo cliente; en una que aloja las cuentas de varios,
el dropdown de "Crear campana" mezclaba las paginas y los numeros de todos. Un
numero de WhatsApp ajeno en un anuncio no es un dato de mas: es que los mensajes
del cliente A le lleguen al B.

Muchos a muchos y no un FK en la tabla original: una pagina puede promocionarse
desde mas de una cuenta del mismo cliente, y el descubrimiento es POR CUENTA, asi
que un solo `ad_account_id` se lo pisaria cada sync.

SEED AMPLIO A PROPOSITO: se enlaza cada pagina/numero con TODAS las cuentas de su
organizacion. Es lo que se veia hasta ahora, y no hay forma de saber
retroactivamente que cuenta descubrio que pagina. Se corrige solo: el sync
reconcilia los enlaces de cada cuenta contra lo que Meta le responde, asi que
tras un ciclo de sync por cuenta queda el mapa real. Empezar fail-closed dejaria
"Crear campana" sin ninguna pagina hasta el sync siguiente.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


_TABLAS = [
    # (tabla de enlace, columna propia, tabla origen, nombre del unique)
    ("meta_page_ad_accounts", "meta_page_id", "meta_pages", "uq_meta_page_ad_account"),
    ("whatsapp_number_ad_accounts", "whatsapp_number_id", "whatsapp_numbers",
     "uq_whatsapp_number_ad_account"),
]


def upgrade() -> None:
    for tabla, columna, origen, unique in _TABLAS:
        op.create_table(
            tabla,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(columna, sa.String(), nullable=False),
            sa.Column("ad_account_id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint([columna], [f"{origen}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ad_account_id"], ["ad_accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(columna, "ad_account_id", name=unique),
        )
        op.create_index(f"ix_{tabla}_{columna}", tabla, [columna])
        op.create_index(f"ix_{tabla}_ad_account_id", tabla, ["ad_account_id"])
        op.create_index(f"ix_{tabla}_organization_id", tabla, ["organization_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        nuevo_id = "gen_random_uuid()::text"
    else:
        nuevo_id = "lower(hex(randomblob(16)))"

    for tabla, columna, origen, _unique in _TABLAS:
        op.execute(sa.text(f"""
            INSERT INTO {tabla} (id, {columna}, ad_account_id, organization_id, created_at)
            SELECT {nuevo_id}, o.id, aa.id, aa.organization_id, CURRENT_TIMESTAMP
            FROM {origen} o
            JOIN ad_accounts aa ON aa.organization_id = o.organization_id
        """))


def downgrade() -> None:
    for tabla, columna, _origen, _unique in _TABLAS:
        op.drop_index(f"ix_{tabla}_organization_id", table_name=tabla)
        op.drop_index(f"ix_{tabla}_ad_account_id", table_name=tabla)
        op.drop_index(f"ix_{tabla}_{columna}", table_name=tabla)
        op.drop_table(tabla)
