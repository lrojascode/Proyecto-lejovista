"""spec12: registro de avisos proactivos del chat

Revision ID: s12e5a9c3f7b4
Revises: s11d4f8a2c6e9

Una sola tabla. El brief mínimo y el contexto extraído de los medios NO llevan
columnas nuevas: viven en `campaign_chat_sessions.slots_json`, que es donde ya
vive el resto del estado multi-turno de la conversación.

Es deliberado. Sacarlos a columnas obligaría a mantener dos cosas en sincronía
para responder «¿qué le falta a esta solicitud?», y el estado del brief se
DERIVA de los slots en vez de duplicarse — dos fuentes sobre lo mismo acaban
discrepando, y la que se quedaría vieja es la que decide si se puede proponer.

`chat_notice_logs` sí necesita ser una tabla: existe para que el mismo aviso no
se repita en cada apertura, y eso exige recordar entre sesiones. Un hallazgo
vigente lo sigue siendo todos los días hasta que alguien lo toque, así que sin
este registro el aviso saldría cada vez y en tres aperturas dejaría de leerse —
y con él se perdería el aviso que sí importaba.
"""
import sqlalchemy as sa
from alembic import op

revision = "s12e5a9c3f7b4"
down_revision = "s11d4f8a2c6e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_notice_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ad_account_id", sa.String(),
                  sa.ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=True),
        # Por usuario: dos personas de la misma organización tienen derecho a
        # ver el aviso cada una. Silenciarlo para la segunda porque la primera
        # ya lo vio esconde el hallazgo justo de quien podría actuar.
        sa.Column("user_id", sa.String(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        # De QUÉ habla el aviso, no cómo está redactado hoy. El texto lleva las
        # cifras del día y cambia solo; si la huella dependiera de él, un
        # céntimo de diferencia lo convertiría en un aviso nuevo y el límite de
        # frecuencia no serviría para nada.
        sa.Column("huella", sa.String(), nullable=False),
        sa.Column("categoria", sa.String(), nullable=False),
        sa.Column("nivel", sa.String(), nullable=True),
        sa.Column("insight_id", sa.String(), nullable=True),
        sa.Column("entregado_en", sa.DateTime(), nullable=True),
    )
    for col in ("organization_id", "ad_account_id", "user_id", "huella",
                "categoria", "entregado_en"):
        op.create_index(f"ix_chat_notice_logs_{col}", "chat_notice_logs", [col])


def downgrade() -> None:
    op.drop_table("chat_notice_logs")
