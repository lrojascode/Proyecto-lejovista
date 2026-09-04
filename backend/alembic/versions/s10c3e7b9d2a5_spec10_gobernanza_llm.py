"""spec10: gobernanza del LLM (catálogo, proveedor explícito, presupuesto, descartes)

Revision ID: s10c3e7b9d2a5
Revises: s09b6d1f4a8e3

Cuatro cosas, en este orden:

1. **Catálogo de modelos** (`ai_model_catalog`) y su siembra con el contenido
   exacto que tenían los diccionarios del código. Sembrar aquí y no al arrancar
   la aplicación es lo que hace que el paso siguiente pueda validar contra él.

2. **Proveedor explícito por Organización.** Se rellena `llm_provider` donde
   estaba NULL con el proveedor que esa Organización usa DE FACTO hoy, para que
   el cambio no altere el comportamiento el primer día. Se deduce igual que lo
   hacía el código: por la forma de la credencial propia si la tiene, y si no,
   por la credencial global activa en este entorno.

   Es la única vez que la deducción por forma de clave decide algo, y a
   propósito: reproducir el comportamiento anterior es exactamente lo que evita
   que el despliegue de esta spec sea, él mismo, el cambio silencioso de
   proveedor que la spec prohíbe.

3. **Presupuesto mensual de IA** y su estado de alertas. Se deja en NULL —sin
   techo— para toda Organización existente: aplicar un bloqueo retroactivo a
   quien nunca lo acordó le cortaría el producto de un despliegue al otro.

4. **Bitácora de cambios de configuración y registro de descartes.**

`estimated_cost_usd_exact` se añade vacía. El histórico en coma flotante NO se
convierte: la precisión perdida no se recupera multiplicando por uno, y
arrastrarla a la columna que decide contra el presupuesto contaminaría la
comparación. El efecto práctico es que el primer mes cuenta desde el despliegue,
lo cual es correcto — nadie acordó un techo antes de que el techo existiera.
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "s10c3e7b9d2a5"
down_revision = "s09b6d1f4a8e3"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.spec10")


def _semilla():
    from app.services.ai.catalogo import SEMILLA
    return SEMILLA


def _proveedor_de_facto(conn, clave_cifrada):
    """El proveedor que esta Organización usa hoy, tal como lo resolvía el código."""
    from app.config import settings

    if clave_cifrada:
        try:
            from cryptography.fernet import Fernet
            clave = Fernet(settings.ENCRYPTION_KEY.encode()).decrypt(
                clave_cifrada.encode()
            ).decode()
        except Exception:
            clave = ""
        k = (clave or "").strip()
        if k.startswith("sk-or-"):
            return "openrouter"
        if k.startswith("sk-"):
            return "openai"
        if k.startswith("AIza"):
            return "gemini_direct"
        return "openrouter"

    # Sin credencial propia: la cascada global, en el orden que tenía el código.
    if settings.OPENAI_API_KEY:
        return "openai"
    if settings.GEMINI_API_KEY:
        return "gemini_direct"
    if settings.OPENROUTER_API_KEY:
        return "openrouter"
    # Ninguna credencial en ningún lado: esta Organización no tiene IA hoy y
    # tampoco la tendrá después. Se deja en NULL para que el resolutor lo diga en
    # voz alta en vez de fallar con un 401 indescifrable.
    return None


def upgrade() -> None:
    op.create_table(
        "ai_model_catalog",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("soporta_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("soporta_json", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("soporta_tools", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("precio_input", sa.Numeric(12, 6), nullable=True),
        sa.Column("precio_output", sa.Numeric(12, 6), nullable=True),
        sa.Column("precio_cache", sa.Numeric(12, 6), nullable=True),
        sa.Column("estado", sa.String(), nullable=False, server_default="disponible"),
        sa.Column("motivo_retiro", sa.String(), nullable=True),
        sa.Column("orden", sa.Numeric(5, 0), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("provider", "model", name="uq_ai_model_catalog_provider_model"),
    )
    op.create_index("ix_ai_model_catalog_provider", "ai_model_catalog", ["provider"])
    op.create_index("ix_ai_model_catalog_model", "ai_model_catalog", ["model"])
    op.create_index("ix_ai_model_catalog_estado", "ai_model_catalog", ["estado"])

    conn = op.get_bind()
    import uuid as _uuid
    from datetime import datetime as _dt

    ahora = _dt.utcnow()
    for entrada in _semilla():
        conn.execute(
            sa.text(
                "INSERT INTO ai_model_catalog "
                "(id, provider, model, label, soporta_vision, soporta_json, "
                " soporta_tools, precio_input, precio_output, precio_cache, "
                " estado, orden, created_at, updated_at) "
                "VALUES (:id, :provider, :model, :label, :vision, true, true, "
                " :input, :output, :cache, 'disponible', :orden, :ahora, :ahora)"
            ),
            {
                "id": str(_uuid.uuid4()),
                "provider": entrada["provider"],
                "model": entrada["model"],
                "label": entrada["label"],
                "vision": bool(entrada.get("vision", False)),
                "input": entrada.get("input"),
                "output": entrada.get("output"),
                "cache": entrada.get("cache"),
                "orden": entrada.get("orden", 0),
                "ahora": ahora,
            },
        )

    # --- 2. Proveedor explícito ---
    filas = conn.execute(sa.text(
        "SELECT id, slug, llm_api_key_encrypted FROM organizations "
        "WHERE llm_provider IS NULL OR llm_provider = ''"
    )).fetchall()
    for fila in filas:
        deducido = _proveedor_de_facto(conn, fila[2])
        if deducido is None:
            logger.warning(
                "La organización '%s' no tiene credencial de IA propia ni global: "
                "queda sin proveedor declarado y el sistema lo dirá explícitamente "
                "en vez de fallar con un 401.", fila[1],
            )
            continue
        conn.execute(
            sa.text("UPDATE organizations SET llm_provider = :p WHERE id = :id"),
            {"p": deducido, "id": fila[0]},
        )
        logger.info(
            "Organización '%s': proveedor de IA fijado explícitamente en '%s' "
            "(el que ya usaba de facto).", fila[1], deducido,
        )

    # --- 3. Presupuesto de IA ---
    op.add_column("organizations", sa.Column("ai_monthly_budget_usd", sa.Numeric(12, 4), nullable=True))
    op.add_column("organizations", sa.Column("ai_budget_alert_state", sa.String(), nullable=True))

    op.add_column("ai_token_usage", sa.Column("prompt_version", sa.String(), nullable=True))
    op.add_column("ai_token_usage", sa.Column("estimated_cost_usd_exact", sa.Numeric(12, 6), nullable=True))
    op.create_index("ix_ai_token_usage_prompt_version", "ai_token_usage", ["prompt_version"])

    # --- 4. Bitácora y descartes ---
    op.create_table(
        "ai_config_changes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("campo", sa.String(), nullable=False),
        sa.Column("valor_anterior", sa.String(), nullable=True),
        sa.Column("valor_nuevo", sa.String(), nullable=True),
        sa.Column("actor_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_email", sa.String(), nullable=True),
        sa.Column("origen", sa.String(), nullable=False, server_default="ui"),
        sa.Column("motivo", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_config_changes_organization_id", "ai_config_changes", ["organization_id"])
    op.create_index("ix_ai_config_changes_campo", "ai_config_changes", ["campo"])
    op.create_index("ix_ai_config_changes_actor_user_id", "ai_config_changes", ["actor_user_id"])
    op.create_index("ix_ai_config_changes_created_at", "ai_config_changes", ["created_at"])

    op.create_table(
        "ai_discards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("ad_account_id", sa.String(), sa.ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("motivo", sa.String(), nullable=False),
        sa.Column("detalle", sa.String(), nullable=True),
        sa.Column("extracto", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("aplicado", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_discards_organization_id", "ai_discards", ["organization_id"])
    op.create_index("ix_ai_discards_ad_account_id", "ai_discards", ["ad_account_id"])
    op.create_index("ix_ai_discards_feature", "ai_discards", ["feature"])
    op.create_index("ix_ai_discards_motivo", "ai_discards", ["motivo"])
    op.create_index("ix_ai_discards_aplicado", "ai_discards", ["aplicado"])
    op.create_index("ix_ai_discards_created_at", "ai_discards", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_discards")
    op.drop_table("ai_config_changes")
    op.drop_index("ix_ai_token_usage_prompt_version", table_name="ai_token_usage")
    op.drop_column("ai_token_usage", "estimated_cost_usd_exact")
    op.drop_column("ai_token_usage", "prompt_version")
    op.drop_column("organizations", "ai_budget_alert_state")
    op.drop_column("organizations", "ai_monthly_budget_usd")
    op.drop_table("ai_model_catalog")
    # `llm_provider` NO se vacía. Lo que se pobló es el proveedor que cada
    # Organización ya usaba; borrarlo la devolvería a la deducción implícita,
    # que es lo que esta spec existe para eliminar.
