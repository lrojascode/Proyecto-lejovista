"""spec 06: el archivo frio conserva lo que lee el aprendizaje

El archivado copiaba un subconjunto de columnas elegido a mano, y se perdian
justo las que deciden si dos campanas son comparables y cuantas conversaciones
reales hubo:

  campaign_snapshots  -> objective, results, effective_status,
                         created_time, start_time, stop_time
  adset_snapshots     -> effective_status, budget, end_time, results
  ad_snapshots        -> effective_status, creative_title, creative_body,
                         creative_cta_type, results

Pasados los meses de retencion en caliente, una campana historica dejaba de
poder usarse como evidencia: el producto perdia su propuesta de valor a medida
que envejecia.

**Aditiva y reversible.** Solo se anaden columnas, nunca se elimina ninguna. Las
filas ya archivadas quedan con estos campos en NULL: el dato NO es recuperable
sin volver a pedirselo a Meta —el archivado borro la fila caliente— y ese hueco
se documenta en la spec en vez de rellenarse con un valor inventado.

Revision ID: s06e2b7c4a9f1
Revises: s05d9a3f6b2e8
"""
import sqlalchemy as sa
from alembic import op

revision = "s06e2b7c4a9f1"
down_revision = "s05d9a3f6b2e8"
branch_labels = None
depends_on = None

_NUEVAS = {
    "campaign_snapshots_archive": [
        ("effective_status", sa.String()),
        ("objective", sa.String()),
        ("created_time", sa.DateTime()),
        ("start_time", sa.DateTime()),
        ("stop_time", sa.DateTime()),
        ("results", sa.Integer()),
    ],
    "adset_snapshots_archive": [
        ("effective_status", sa.String()),
        ("budget", sa.Float()),
        ("end_time", sa.DateTime()),
        ("results", sa.Integer()),
    ],
    "ad_snapshots_archive": [
        ("effective_status", sa.String()),
        ("creative_title", sa.String()),
        ("creative_body", sa.Text()),
        ("creative_cta_type", sa.String()),
        ("results", sa.Integer()),
    ],
}


def upgrade() -> None:
    for tabla, columnas in _NUEVAS.items():
        for nombre, tipo in columnas:
            op.add_column(tabla, sa.Column(nombre, tipo, nullable=True))


def downgrade() -> None:
    for tabla, columnas in _NUEVAS.items():
        for nombre, _ in columnas:
            op.drop_column(tabla, nombre)
