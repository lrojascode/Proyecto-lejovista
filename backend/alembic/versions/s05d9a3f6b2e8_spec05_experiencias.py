"""spec 05: lado del producto por membresia y estado del alta asistida

Dos columnas (docs/specs/05-experiencias-cliente-agencia-roles-y-modulos.md):

1. `organization_users.access_kind` — de que LADO entra cada membresia:
   `CLIENT` o `AGENCY`. No es un rol mas: los tres roles acotan que puede hacer
   alguien; esto acota a que altura del producto entra.

   **Se puebla como `AGENCY` a proposito.** Al desplegar, nadie pierde acceso:
   todos conservan exactamente la superficie que tenian. Convertir una membresia
   en `CLIENT` es un acto deliberado de la agencia, cliente por cliente. Al reves
   —poblar como CLIENT— dejaria al operador sin herramientas el dia del
   despliegue, y con la vista simplificada de un cliente encima.

2. `organizations.onboarding_state` — que pasos del alta se validaron contra el
   sistema externo real. Vacio = alta no iniciada.

   Las organizaciones que YA existen se marcan con el alta completa: estan en
   produccion y funcionando, asi que exigirles pasos retroactivos bloquearia
   invitaciones legitimas por un requisito que se invento despues.

Migracion aditiva: la imagen anterior funciona sobre este esquema sin cambios.

Revision ID: s05d9a3f6b2e8
Revises: s03c7f2a5d8e4
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "s05d9a3f6b2e8"
down_revision = "s03c7f2a5d8e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_users",
        sa.Column("access_kind", sa.String(length=10), nullable=False, server_default="AGENCY"),
    )
    op.add_column("organizations", sa.Column("onboarding_state", sa.JSON(), nullable=True))

    conn = op.get_bind()
    marca = json.dumps({
        "meta": {"estado": "validado", "detalle": "organizacion anterior al alta asistida", "actor": None, "en": None},
        "ia": {"estado": "validado", "detalle": "organizacion anterior al alta asistida", "actor": None, "en": None},
        "whatsapp": {"estado": "validado", "detalle": "organizacion anterior al alta asistida", "actor": None, "en": None},
    })
    conn.execute(
        sa.text("UPDATE organizations SET onboarding_state = CAST(:v AS JSON)"),
        {"v": marca},
    )


def downgrade() -> None:
    op.drop_column("organizations", "onboarding_state")
    op.drop_column("organization_users", "access_kind")
