"""spec 02: limite mensual exacto y moneda de la cuenta

Dos columnas que sostienen la compuerta de presupuesto mensual
(docs/specs/02-limite-presupuestario-mensual-y-dinero-exacto.md):

1. `organizations.max_budget_exact` (NUMERIC 14,2) — el limite que DECIDE. La
   columna `max_budget` es `Float`, y una comparacion en coma flotante binaria
   puede autorizar un gasto que excede el limite o rechazar uno que cabe
   exactamente. En una compuerta financiera eso es un fallo de autorizacion.

2. `ad_accounts.currency` — la moneda de la cuenta segun Meta. Una organizacion
   con cuentas en monedas distintas se rechaza en vez de sumar cifras
   incomparables.

**Expand-and-contract.** `max_budget` se conserva y se sigue escribiendo, asi
que la imagen anterior funciona sobre este esquema. Se retira en la contraccion,
cuando ya no quede codigo que la lea.

El poblado inicial pasa por `str(...)` a proposito: `Decimal(1000.55)` arrastra
el residuo binario del float, `Decimal("1000.55")` no. `repr` de un float en
Python garantiza el round-trip, asi que reconstruye el valor que se pretendia
guardar, no su aproximacion.

Revision ID: s02b5e8a1c4f7
Revises: s01a4d7e2b9c1
"""
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "s02b5e8a1c4f7"
down_revision = "s01a4d7e2b9c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("max_budget_exact", sa.Numeric(14, 2), nullable=True)
    )
    op.add_column("ad_accounts", sa.Column("currency", sa.String(length=8), nullable=True))

    conn = op.get_bind()
    for org_id, max_budget in conn.execute(
        sa.text("SELECT id, max_budget FROM organizations")
    ).fetchall():
        if max_budget is None:
            continue
        exacto = Decimal(repr(float(max_budget))).quantize(Decimal("0.01"))
        conn.execute(
            sa.text("UPDATE organizations SET max_budget_exact = :v WHERE id = :i"),
            {"v": str(exacto), "i": org_id},
        )


def downgrade() -> None:
    op.drop_column("ad_accounts", "currency")
    op.drop_column("organizations", "max_budget_exact")
