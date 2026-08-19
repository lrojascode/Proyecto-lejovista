# 07 · Modelos

Modelos SQLAlchemy 2.0 declarativos (`Mapped[...] / mapped_column`) en `backend/app/models/`. Todos heredan de `Base` (`app/database.py`). Los PK son UUID string (`default=lambda: str(uuid.uuid4())`). Todos se registran en `app/models/__init__.py`.

## 1. Identidad y multi-tenant

### `Organization` (`organizations`)
Tenant del SaaS. Campos: `id`, `slug` (único), `name`, `ai_model` (default `google/gemini-3.5-flash`), `max_budget` (límite financiero de seguridad, default 1000), `status` (`ACTIVE`), `industry` (rubro → políticas de Meta), `campaign_rules_overrides`/`ai_thresholds_overrides` (JSON, default `{}` — overrides parciales por tenant sobre `TENANT_CAMPAIGN_RULES`/`AIThresholds`, M-2), `created_at`. Relaciones (cascade delete): `users`, `ad_accounts`, `campaign_snapshots`, `insights`, `actions`, `sync_jobs`, `meta_credentials`, `meta_pages`.

### `User` (`users`)
Usuario global. Campos: `id`, `email` (único), `hashed_password` (bcrypt), `full_name`, `created_at`. Relación `organizations` (`OrganizationUser`).
- **Propiedad `organization_id`**: devuelve la organización **activa** de la sesión (`_active_org_id`, fijada por `get_current_user`, `S-8`), con fallback a la primera. Es el pivote del aislamiento multi-tenant.

### `OrganizationUser` (`organization_users`)
N:M usuario↔org con `role` (`ADMIN`/`OPERATOR`/`VIEWER`). FKs `CASCADE`.

## 2. Conexión con Meta

### `MetaCredential` (`meta_credentials`)
Token de Meta cifrado. Campos: `access_token_encrypted`, `token_expires_at`, `status` (`ACTIVE`/`EXPIRED`/`REVOKED`).
- Métodos `set_access_token` / `get_access_token` cifran/descifran con **Fernet** (`ENCRYPTION_KEY`). TTL por defecto 60 días (long-lived token de Meta). Meta no usa refresh tokens (`set_refresh_token` es no-op).
- Propiedad `is_expired`.

### `AdAccount` (`ad_accounts`)
Cuenta publicitaria conectada. Campos: `organization_id`, `meta_credential_id` (`SET NULL`), `platform` (`META`/`GOOGLE`), `external_account_id` (`act_XXX`), `name`, narrativa cacheada (`cached_narrative`, `narrative_generated_at`, `narrative_model`, `narrative_provider`, `narrative_prompt_version` — trazabilidad `D-4`/`AI-4`), `status` (`ACTIVE`/`EXPIRED`/`PAUSED`/`SUSPENDED`/`ARCHIVED`), `spend_cap`, `amount_spent`. Constraint `uq_org_platform_acc`.
- Métodos proxy `get/set_access_token` delegan en `meta_credential`.

### `MetaPage` (`meta_pages`)
Página de Facebook descubierta por el sync. Campos: `external_page_id`, `name`, `instagram_actor_id`, `whatsapp_number` (para el dropdown de creación), `status`. Constraint `uq_org_meta_page`.

### `MetaCreative` (`meta_creatives`)
Inventario de imágenes (adimages): `image_hash`, `name`, `url`, `width`, `height`, `meta_created_time`. Constraint `uq_creative_acc_hash`.

### `CustomAudience` (`custom_audiences`)
Audiencias personalizadas: `external_id`, `name`, `subtype` (`CUSTOM`/`LOOKALIKE`/`WEBSITE`/…), `description`, `approximate_count`. Constraint `uq_audience_acc_external`.

## 3. Datos analíticos (snapshots)

### `CampaignSnapshot` (`campaign_snapshots`)
Métrica diaria por campaña. Métricas: `budget`, `spend`, `impressions`, `reach`, `clicks`, `conversions` (leads), `conversations` (mensajes WhatsApp), `cpl`, `ctr`, `frequency`. Breakdowns JSON: `placements_breakdown`, `demographics_breakdown` (edad), `extra_breakdowns` (género/dispositivo/región). Índices: `idx_snapshot_org_date`, `idx_snapshot_acc_date`; unique `uq_snapshot_acc_campaign_date`.

### `AdsetSnapshot` (`adset_snapshots`)
Métrica diaria por adset + `budget` (nominal; en cuentas ABO el presupuesto vive aquí, `Bug 5`) + `targeting_summary` (JSON). Unique `uq_adset_snapshot_acc_adset_date`.

### `AdSnapshot` (`ad_snapshots`)
Métrica diaria por anuncio + **copy real** del creativo (`creative_title`, `creative_body`, `creative_cta_type`) para que la IA se inspire en lo que funcionó. Unique `uq_ad_snapshot_acc_ad_date`.

### `AdAccountSpendMonthly` (`ad_account_spend_monthly`)
Gasto total por mes calendario (`year_month` "YYYY-MM", `spend`, `closed`). Backfill único por cuenta; luego solo se refresca el mes en curso. Unique `uq_ad_account_year_month`.

### Tablas de archivo (`*_snapshots_archive`)
`CampaignSnapshotArchive`, `AdsetSnapshotArchive`, `AdSnapshotArchive`: réplica de las columnas de negocio + `original_id` + `archived_at`, **sin FKs ni uniques**. Pobladas por el archivador (`D-3`).

## 4. Inteligencia y acciones

### `AIInsight` (`ai_insights`)
Hallazgo de Capa 1/2. Campos: `level` (`INFO`/`WARNING`/`CRITICAL`), `category` (`BUDGET`/`PLACEMENT`/`SATURATION`/`PERFORMANCE`/`ACCOUNT`), `title`, `description`, `suggested_action_type`, `is_dismissed`, `campaign_id`.

### `AIAction` (`ai_actions`)
Acción propuesta por la IA. Campos: `action_type` (`MODIFY_BUDGET`/`PAUSE_CAMPAIGN`/`MODIFY_PLACEMENTS`/`MODIFY_TARGETING`/`RESUME_CAMPAIGN`/`CREATE_CAMPAIGN`), `risk_level` (`LOW`/`MEDIUM`/`HIGH`), `target_campaign_id`, `target_adset_id`, `parameters` (JSON, incl. `backup_state` para rollback), `planned_api_calls` (JSON), ciclo de vida (`status`, `proposed_at`, `approved_at/by`, `rejected_at`, `reject_reason`, `executed_at`, `execution_result`), `estimated_impact`, `confidence`.

### `CampaignLearningLog` (`campaign_learning_logs`)
Memoria de aprendizaje de creación. Campos: `product`, `zone`, `params_json`, `analysis_json` (razonamiento), `maturity_json` (convergencia), `risk_level`, `status` (`PROPOSED`→`CREATED`→`EVALUATED`), `outcome_json` (veredicto real: WIN/LOSS/NEUTRAL/NOT_ACTIVATED), `outcome_recorded_at`.

### `AITokenUsage` (`ai_token_usage`)
Consumo de una llamada real al LLM: `feature`, `provider`, `model`, tokens (`prompt`/`completion`/`reasoning`/`total`), `used_cot`, `estimated_cost_usd` (nullable si el modelo no está tarifado). Solo se graban llamadas reales.

## 5. Creación de campañas

### `CampaignDraft` (`campaign_drafts`)
Estado de la Saga de creación. Campos: `created_by_user_id`, `name`, `payload` (JSON con la config; `payload["result"]` acumula los IDs de Meta paso a paso), `status` (`DRAFT`/`PENDING_CREATION`/`CREATED`/`FAILED`), `error_message`.

## 6. Operación y auditoría

### `SyncJob` (`sync_jobs`)
Registro de cada sincronización: `sync_type` (`FULL`/`INCREMENTAL`/`BACKFILL`/`WEEKLY`), `status` (`PENDING`/`RUNNING`/`COMPLETED`/`FAILED`), `started_at`, `completed_at`, `error_message`, `records_synced`, `payload_json` (JSONB, Master Brief). Un `BACKFILL` completado marca que la cuenta ya cargó su ventana diaria de retención. El `/health/deep` consulta el último `COMPLETED`.

### `APICallLog` (`api_call_logs`)
Auditoría HTTP a Meta: `method`, `endpoint`, `params` (token enmascarado), `status_code`, `response_preview`, `timestamp`.

## 7. Convenciones de los modelos

- **PK UUID string** en todas las tablas → portabilidad y no adivinable.
- **`ondelete`** explícito: `CASCADE` para datos derivados del tenant/cuenta; `SET NULL` para referencias blandas (credencial, draft, actor). Nota: `api_call_logs.ad_account_id` **no** define `ondelete`, motivo por el que las cuentas se **archivan** en vez de borrarse.
- **Breakdowns/parametrizaciones** como columnas `JSON`/`JSONB` en vez de tablas normalizadas (lectura conjunta, esquema flexible de Meta).
- **Métrica primaria unificada** (`E-2`): `conversations or conversions` (conversaciones de WhatsApp, con fallback a leads) en todo el sistema.
