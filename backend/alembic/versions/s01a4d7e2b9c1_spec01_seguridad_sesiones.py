"""spec 01: corte de sesiones, unicidad de membresia y token de invitacion hasheado

Tres cambios de esquema que sostienen la spec 01 (docs/specs/01-...):

1. `users.sessions_valid_from` — corte de validez por usuario. Permite revocar
   TODAS las sesiones (cambio/restablecimiento de contrasena, suspension) sin
   enumerar jti, y sobrevive a una caida de Redis porque vive en la base.

2. Restriccion unica sobre `organization_users(user_id, organization_id)` — la
   comprobacion en la aplicacion no impedia que dos aceptaciones concurrentes de
   la misma invitacion insertaran dos filas. Se deduplica antes de crearla,
   conservando la membresia mas antigua y el rol de MAYOR rango entre las
   duplicadas: bajar el rango de alguien en silencio durante una migracion seria
   peor que dejarlo como estaba.

3. `invitations.token_hash` — el token dejaba de ser secreto en cuanto alguien
   leia la tabla. Se guarda su SHA-256, igual que los tokens de recuperacion de
   contrasena.

**Expand-and-contract.** `invitations.token` se conserva (ahora nullable) y se
rellena `token_hash` a partir de el, asi que la imagen anterior sigue
funcionando sobre este esquema. La columna `token` se retira en una migracion
posterior, cuando ya no quede codigo que la lea.

Revision ID: s01a4d7e2b9c1
Revises: b1c2d3e4f5a6
"""
import hashlib

import sqlalchemy as sa
from alembic import op

revision = "s01a4d7e2b9c1"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


# Mismo orden que app/utils/security.ROLE_RANK. Duplicado a proposito: una
# migracion no debe importar codigo de la aplicacion, que cambia bajo sus pies.
_ROLE_RANK = {"VIEWER": 0, "OPERATOR": 1, "ADMIN": 2}


def upgrade() -> None:
    # --- 1. Corte de sesiones por usuario ---------------------------------
    op.add_column("users", sa.Column("sessions_valid_from", sa.DateTime(), nullable=True))

    # --- 2. Unicidad de membresia -----------------------------------------
    conn = op.get_bind()
    duplicadas = conn.execute(sa.text("""
        SELECT user_id, organization_id
        FROM organization_users
        GROUP BY user_id, organization_id
        HAVING COUNT(*) > 1
    """)).fetchall()

    for user_id, org_id in duplicadas:
        filas = conn.execute(sa.text("""
            SELECT id, role, created_at
            FROM organization_users
            WHERE user_id = :u AND organization_id = :o
            ORDER BY created_at ASC, id ASC
        """), {"u": user_id, "o": org_id}).fetchall()

        # Se conserva la mas antigua; hereda el rol de mayor rango del grupo.
        sobrevive = filas[0]
        mejor_rol = max(
            (f[1] for f in filas),
            key=lambda r: _ROLE_RANK.get(r, -1),
        )
        conn.execute(
            sa.text("UPDATE organization_users SET role = :r WHERE id = :i"),
            {"r": mejor_rol, "i": sobrevive[0]},
        )
        for f in filas[1:]:
            conn.execute(
                sa.text("DELETE FROM organization_users WHERE id = :i"), {"i": f[0]}
            )

    op.create_unique_constraint(
        "uq_org_user_membership", "organization_users", ["user_id", "organization_id"]
    )

    # --- 3. Token de invitacion hasheado ----------------------------------
    op.add_column("invitations", sa.Column("token_hash", sa.String(length=64), nullable=True))

    filas = conn.execute(
        sa.text("SELECT id, token FROM invitations WHERE token IS NOT NULL")
    ).fetchall()
    vistos = set()
    for inv_id, token in filas:
        h = hashlib.sha256(token.encode()).hexdigest()
        if h in vistos:
            # Imposible en la practica (`token` era unico), pero el indice unico
            # que viene despues no perdona: se deja sin hash antes que romper.
            continue
        vistos.add(h)
        conn.execute(
            sa.text("UPDATE invitations SET token_hash = :h WHERE id = :i"),
            {"h": h, "i": inv_id},
        )

    op.create_index(
        "ix_invitations_token_hash", "invitations", ["token_hash"], unique=True
    )

    # Fase de EXPANSION: `token` pasa a nullable y deja de escribirse, pero se
    # conserva para que la imagen anterior siga sirviendo si hay que volver
    # atras. La contraccion (DROP COLUMN) es otra migracion.
    with op.batch_alter_table("invitations") as batch:
        batch.alter_column("token", existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("invitations") as batch:
        batch.alter_column("token", existing_type=sa.String(length=100), nullable=False)
    op.drop_index("ix_invitations_token_hash", table_name="invitations")
    op.drop_column("invitations", "token_hash")
    op.drop_constraint("uq_org_user_membership", "organization_users", type_="unique")
    op.drop_column("users", "sessions_valid_from")
