# 06 · API

API REST versionada bajo `/api/v1` + un WebSocket bajo `/ws`. Todos los endpoints (salvo auth, healthchecks y el WebSocket handshake) requieren `Authorization: Bearer <access_token>`.

## 1. Convenciones transversales

- **Auth**: header `Authorization: Bearer <JWT>`. Ver [08-autenticacion](08-autenticacion.md).
- **Tenant**: header `X-Tenant-Slug` (subdominio) o claim `org` del token — resuelve la organización activa.
- **Cuenta activa**: header `X-Ad-Account-Id` — resuelve la cuenta publicitaria (obligatorio si la org tiene 2+ cuentas; auto si tiene 1).
- **Errores**: `{"detail": "..."}` (string) o `{"detail": {"code": "...", "message": "..."}}` (estructurado).
- **Documentación viva**: `GET /docs`, `/redoc`, `/openapi.json` si `ENABLE_API_DOCS=true`.

Códigos frecuentes: `400` (regla de negocio), `401` (sin/mal token), `403` (tenant/cuenta ajena), `404` (recurso no visible para el tenant), `429` (cooldown/rate limit), `502` (Meta rechazó).

## 2. Healthchecks (sin auth)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` | Liveness `{status, mock_mode, base_domain}` |
| GET | `/health/deep` | Salud del pipeline: `degraded` si no hubo sync exitoso en 24 h |

## 3. Autenticación — `/api/v1/auth`

| Método | Ruta | Body | Notas |
|--------|------|------|-------|
| POST | `/auth/signup` | `UserCreate` | Crea org (o la reutiliza por slug) + usuario `ADMIN`. Rate-limit 5/h por IP. Slugs reservados: `api, www, admin, localhost, static` |
| POST | `/auth/login` | `UserLogin` | Rate-limit 10/5min por IP. Devuelve access+refresh |
| POST | `/auth/refresh` | `{refresh_token}` | Rota el refresh (revoca el usado, `S-9`) |
| POST | `/auth/logout` | `{refresh_token}` | Revoca el refresh en Redis |
| GET | `/auth/me` | — | Usuario actual enriquecido (rol de la org activa) |

Respuesta de signup/login/refresh: `TokenResponse { access_token, refresh_token, token_type, user }`.

## 4. Organizaciones y cuentas — `/api/v1/organizations`

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/organizations/ai-models` | Modelos IA del **proveedor activo** (según API key) |
| GET | `/organizations/my` | Org actual |
| PUT | `/organizations/my` | Actualiza `name`, `ai_model`, `max_budget`, `industry` |
| GET | `/organizations/my/policies` | Recordatorios de políticas de Meta por rubro + rubros disponibles |
| GET | `/organizations/my/ad-accounts?include_archived=` | Cuentas (excluye archivadas por defecto) |
| POST | `/organizations/my/ad-accounts` | Conecta cuenta (cifra token) y dispara sync **FULL** |
| POST | `/organizations/my/ad-accounts/{id}/archive` | Soft-delete (`status=ARCHIVED`) |
| POST | `/organizations/my/ad-accounts/{id}/restore` | Revierte el archivado |
| GET | `/organizations/my/pages` | Páginas FB descubiertas (con WhatsApp) |
| PATCH | `/organizations/my/pages/{page_id}/whatsapp` | Fija manualmente el WhatsApp de una página |

## 5. Campañas — `/api/v1/campaigns`

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/campaigns/recent` | Último snapshot por campaña (ventana 7 días) + agregado 7d real (`spend_7d`, `conversations_7d`, `cost_per_conv_7d`) |
| GET | `/campaigns/{campaign_id}/history` | Serie diaria de snapshots (para gráficos) |
| GET | `/campaigns/api-call-history?limit=` | Últimas llamadas a Meta de la cuenta |
| POST | `/campaigns/sync-trigger` | Encola sync **INCREMENTAL** de todas las cuentas activas (cooldown 60 s) |
| GET | `/campaigns/suggestion?ad_account_id=&product_name=` | Sugerencia estructural determinista (3A) + copy del LLM (3B) + riesgo |
| POST | `/campaigns/risk-preview` | Riesgo con los valores actuales del formulario (no escribe) |
| POST | `/campaigns/upload-image` | Sube imagen (JPG/PNG/WebP, ≤8 MB) → `image_path` |
| POST | `/campaigns/` | **Inicia la Saga de creación** (202). Anti-bloqueo: máx. 5 campañas/24h por org |
| GET | `/campaigns/drafts/{draft_id}` | Estado real de la Saga (`payload.result` con IDs de Meta) |
| GET | `/campaigns/learning-history?limit=` | Historial de entrenamiento de la IA (qué propuso y por qué) |

`POST /campaigns/` recibe `CampaignCreateRequest` (`ad_account_id`, `page_id`, `product_name`, `budget`, `days`, `whatsapp_number`, imagen/video, `copy_text`, `headline?`, `description?`). Ver [20-creacion-campanas](20-creacion-campanas.md).

## 6. Insights (Diagnósticos) — `/api/v1/insights`

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/insights` | Insights activos (no descartados) de la cuenta activa |
| POST | `/insights/{id}/dismiss` | Descarta un insight |
| POST | `/insights/analyze-trigger` | Encola el análisis de IA en background (cooldown 120 s) |

## 7. Acciones sugeridas — `/api/v1/actions`

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/actions` | Acciones `PROPOSED` |
| GET | `/actions/history` | Acciones ya procesadas (≠ `PROPOSED`) |
| POST | `/actions/{id}/approve` | Aprueba (encola ejecución en Celery) o rechaza (`ActionApprove {approve, reject_reason?}`) |
| GET | `/actions/{id}/status` | Estado y `execution_result` de una acción |

Ciclo de vida: `PROPOSED → APPROVED → EXECUTING → COMPLETED/FAILED` (o `REJECTED`, o `NEEDS_INPUT` si la IA no puede resolver página/creativo sin ambigüedad).

## 8. Reportes — `/api/v1/reports`

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/reports/brief-data?window_days=7\|30\|90` | Master Brief: narrativa cacheada + metadatos de la narrativa + reporte estructurado completo + `campaigns_data` crudo |
| GET | `/reports/pdf?window_days=` | PDF real (WeasyPrint) del reporte |
| GET | `/reports/kpis-summary` | Resumen liviano para el dashboard (KPIs 7d + Δ%, alertas críticas, último sync) |
| GET | `/reports/spend-history` | Histórico de inversión mensual |

> `brief-data` es **solo lectura**: si la narrativa aún no existe devuelve `narrative_pending: true` para que el frontend dispare el análisis. La narrativa la genera el `AIEngine` en background.

El objeto `report` (construido por `ReportBuilder`, ver [21-producto-datos-visualizados](21-producto-datos-visualizados.md)) incluye: `account`, `kpis` (con `deltas_pct` vs período previo del mismo tamaño), `campaigns` (con `label`, `*_delta_pct` y `created_by_ai`), `adsets`, `ads` (con copy del creativo), `segmentation` (placements/age/gender/device/region), `audiences`, `creatives`, `alerts` (`red`/`green`), `optimal_params`, y dos listas de frases listas para mostrar: **`findings`** ("lo más importante que encontramos") y **`recommendations`** ("qué hacer con las nuevas campañas"). Además del `report`, el endpoint devuelve `narrative`, `narrative_pending`, `narrative_generated_at`, `narrative_model`, `narrative_provider`, `narrative_prompt_version` y `campaigns_data` (resumen crudo que alimenta al LLM).

## 9. Uso de IA — `/api/v1/ai-usage`

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/ai-usage?days=30` | Consumo de tokens agregado por `feature` (`master_brief`, `campaign_copy`, …): llamadas, tokens prompt/respuesta/razonamiento, CoT, costo estimado USD |

## 10. WebSocket — `/ws/notifications/{tenant_id}`

- Handshake requiere `?token=<JWT>`; el backend valida que el usuario pertenezca al tenant (`tenant_id = organization_id`). Si no, cierra con código `4401`.
- El servidor retransmite eventos publicados por los workers en el canal Redis `ws_notifications`.
- Ping/pong: el cliente puede enviar `"ping"` y recibe `"pong"`.

Tipos de evento (`event_type`): `sync_status`, `analysis_status`, `campaign_creation`.

```json
{ "tenant_id": "...", "event_type": "analysis_status", "job_id": "...", "status": "COMPLETED",
  "insights_count": 5, "actions_proposed": 2 }
```

## 11. Mapa router → prefijo

| Router | Prefijo | Tag |
|--------|---------|-----|
| `auth.py` | `/api/v1/auth` | auth |
| `organizations.py` | `/api/v1/organizations` | organizations |
| `campaigns.py` | `/api/v1/campaigns` | campaigns |
| `insights.py` | `/api/v1/insights` | insights |
| `actions.py` | `/api/v1/actions` | actions |
| `reports.py` | `/api/v1/reports` | reports |
| `ai_usage.py` | `/api/v1/ai-usage` | ai-usage |
| `ws.py` | `/ws` | websockets |
