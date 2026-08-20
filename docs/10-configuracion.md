# 10 · Configuración

Toda la configuración del backend vive en `app/config.py` (`pydantic-settings`), cargada desde `.env` (buscado en la raíz del proyecto, en `backend/` o en el CWD). Las variables `NEXT_PUBLIC_*` del frontend se inlinean en **build time**.

## 1. Variables de entorno del backend

### Modo de operación
| Variable | Default | Descripción |
|----------|---------|-------------|
| `MOCK_SERVICES` | `true` | Simula Meta y LLM. En local permite probar todo sin credenciales |
| `ENABLE_META_WRITEBACK` | `false` | Habilita **escrituras reales** a Meta (independiente de `MOCK_SERVICES`) |
| `WRITEBACK_ACCOUNT_ALLOWLIST` | `""` | `AdAccount.id` (no `external_account_id`) separados por coma; restringe la escritura real a cuentas piloto (`F-2`) |
| `AUTO_CREATE_TABLES` | `false` | `create_all` al arrancar (solo dev/test; en prod usar Alembic) |
| `ENABLE_API_DOCS` | `true` | Expone `/docs`, `/redoc`, `/openapi.json`. **Apagar** al exponer públicamente |

### Dominio y red
| Variable | Default | Descripción |
|----------|---------|-------------|
| `BASE_DOMAIN` | `localhost:3000` | Dominio base para CORS y URLs |
| `API_URL` | `http://api.localhost:8000` | URL del API (server-side) |
| `TUNNEL_ORIGIN_REGEX` | `""` | Regex extra de origen CORS (túnel ngrok/Cloudflare) |

### Base de datos y Redis
| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://…` | Conexión async a Postgres |
| `REDIS_URL` | `redis://:…@redis:6379/0` | Broker Celery, cache, pubsub, blocklist |
| `CELERY_RESULT_BACKEND_URL` | `…/1` | Backend de resultados de Celery |

### Seguridad
| Variable | Default | Descripción |
|----------|---------|-------------|
| `SECRET_KEY` | *(obligatoria)* | Firma de JWT. `openssl rand -hex 64` |
| `ENCRYPTION_KEY` | *(obligatoria)* | Clave Fernet para cifrar tokens de Meta |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Vida del access token (24 h) |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080` | Vida del refresh token (7 días) |

### Meta Ads
| Variable | Default | Descripción |
|----------|---------|-------------|
| `META_APP_ID` | `mock_app_id` | App ID de Meta |
| `META_APP_SECRET` | `mock_app_secret` | App Secret |
| `META_API_VERSION` | `v25.0` | Versión de la Graph/Marketing API (las versiones expiran ~2 años) |

### IA / LLM
| Variable | Default | Descripción |
|----------|---------|-------------|
| `OPENAI_API_KEY` | `""` | Si está, el proveedor activo es **OpenAI** (mayor prioridad) |
| `GEMINI_API_KEY` | `""` | Si está (y sin OpenAI), proveedor **Gemini directo** |
| `OPENROUTER_API_KEY` | `""` | Fallback: proveedor **OpenRouter** |
| `DEFAULT_AI_MODEL` | `google/gemini-2.5-flash` | Modelo por defecto si la org no fija otro |

> El proveedor se auto-selecciona por prioridad OpenAI > Gemini > OpenRouter (`OpenRouterClient.__init__`). El modelo por organización se guarda en `Organization.ai_model`, pero el selector solo ofrece modelos del proveedor activo.

### Observabilidad y retención
| Variable | Default | Descripción |
|----------|---------|-------------|
| `SENTRY_DSN` | `""` | Vacío = Sentry desactivado |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `SNAPSHOT_RETENTION_DAYS` | `180` | Días antes de archivar snapshots (`D-3`) |
| `MEDIA_ROOT` | `/app/media` | Carpeta de imágenes subidas (compartida backend↔worker de creación) |

## 2. Variables del frontend (`NEXT_PUBLIC_*`)

Inlinadas en el bundle del cliente durante `npm run build` (build args del `Dockerfile` / `build-push.sh`):

| Variable | Descripción |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | URL base del API |
| `NEXT_PUBLIC_BASE_DOMAIN` | Dominio base (para resolver el tenant slug) |
| `NEXT_PUBLIC_WS_URL` | URL del WebSocket (`ws://`/`wss://`) |
| `NEXT_PUBLIC_SINGLE_HOST_MODE` | `true` para modo túnel (origen relativo, ruteo por path) |

> **Cambiar de dominio en producción requiere reconstruir la imagen del frontend** (`build-push.sh` inyecta `https://api.<BASE_DOMAIN>` y `wss://api.<BASE_DOMAIN>`).

## 3. Puertos (compose local)

| Variable | Default | Servicio |
|----------|---------|----------|
| `DB_PORT` | `5433` | Postgres (mapeado a 5432 interno; 5433 evita colisión en macOS) |
| `REDIS_PORT` | `6379` | Redis |
| `BACKEND_PORT` | `8000` | FastAPI |
| `FRONTEND_PORT` | `3000` | Next.js |
| `NGINX_PORT` / `NGINX_SSL_PORT` | `80` / `443` | Nginx |

En local, los puertos de db/redis/backend/frontend se publican **solo en `127.0.0.1`**.

## 4. Config de tests (`conftest.py`)

Fija por defecto (antes de importar la app): `SECRET_KEY`, `ENCRYPTION_KEY` (Fernet de ejemplo), `MOCK_SERVICES=true`, `ENABLE_META_WRITEBACK=false`, `AUTO_CREATE_TABLES=true`. Usa SQLite async en archivo temporal.

## 5. Reglas de negocio parametrizadas

Además de las env vars, hay configuración de dominio en código:
- `app/tenant_rules/campaign_defaults.py` — `TENANT_CAMPAIGN_RULES` (geo Lima, objetivo `OUTCOME_LEADS`, `CONVERSATIONS`→`WHATSAPP`, placements requeridos/prohibidos, `PAUSED` por defecto). Hoy globales, con TODO para moverlas a una tabla por tenant.
- `app/services/ai/thresholds.py` — `AIThresholds` (umbrales de las reglas y estadística), con punto de extensión `get_thresholds_for_org`.
- `app/services/ai/token_tracking.py` — `MODEL_PRICING` (precios por modelo para estimar costo).
- `app/services/policy_catalog.py` — políticas de Meta por rubro (contenido curado).

---

## Settings agregados en 2026-08

| Setting | Default | Qué gobierna |
|---|---|---|
| `HOT_DAILY_RETENTION_DAYS` | 400 | Hasta dónde baja el backfill diario. 12 meses + 1 de margen, para que "este mes contra el mismo del año pasado" se resuelva en caliente |
| `SNAPSHOT_RETENTION_DAYS` | 400 | A partir de cuándo el archivador mueve filas a las tablas `_archive` |
| `STATISTICS_WINDOW_DAYS` | 180 | Ventana de la Capa 2 estadística. Series muy largas diluyen las anomalías recientes |
| `CHAT_MAX_HISTORY_DAYS` | 400 | Techo de la ventana de consulta con detalle diario en el chat |

Los cuatro eran **un solo número** (`SNAPSHOT_RETENTION_DAYS = 180`) que
gobernaba cuatro decisiones que no tienen por qué coincidir, así que cambiar una
arrastraba las otras tres.

### Reglas por organización (`app/tenant_rules/campaign_defaults.py`)

| Regla | Default | Qué hace |
|---|---|---|
| `use_age_targeting` | `False` | Segmentar por edad. Apagado porque la data es plana entre rangos y hacerlo obliga a apagar Advantage+ |
| `min_daily_budget_for_learning` | `16.0` | Piso diario por conjunto. **Avisa, no bloquea** |
| `geo_countries` | `[]` | Países (ISO). Vacío = se usa `default_geo_region_key` |
| `campaign_name_type_token` | `"MSJ"` | Segmento de tipo del nombre de campaña |

### Módulos por organización

`Organization.enabled_modules` (JSON). **NULL o vacío = todos habilitados**,
mismo contrato que `campaign_rules_overrides`. El catálogo está en
`app/tenant_rules/modules.py` y se administra desde `/admin` — no desde
`PATCH /organizations/my`, para que el módulo siga siendo una decisión
comercial y no una preferencia del cliente.

`User.is_platform_admin` habilita `/admin`. **No da acceso a los datos de
ningún tenant.**
