# 02 · Estructura del Repositorio

Monorepo con dos aplicaciones (`backend/`, `frontend/`) e infraestructura compartida en la raíz. No usa un gestor de monorepo (Nx/Turborepo); cada app tiene su propio toolchain (pip/venv y npm) y su propio `Dockerfile`.

## 1. Raíz

```
Proyecto lejovista/
├── backend/                    # API FastAPI + workers Celery + servicios
├── frontend/                   # App Next.js 16
├── nginx/                      # Configuración del reverse proxy
├── docs/                       # Esta documentación
├── docker-compose.yml          # Stack de desarrollo local
├── docker-compose.tunnel.yml   # Override para exponer vía túnel (single-host)
├── build-push.sh               # Compila y publica imágenes a GHCR (fallback manual)
├── .env / .env.example         # Variables de entorno
├── HANDOFF.md                  # Bitácora de auditoría (hallazgos)
├── plan-implementacion-auditoria.md
├── PLAYBOOK-META-ADS-IA.md     # Reglas de negocio de Meta Ads
├── PLAN-CREACION-CAMPANAS.md   # Diseño del flujo de creación
├── NotasReunionAlejandroMarketink.md
├── posibles-mejoras.md
└── TUNNEL.md                   # Guía de exposición vía túnel
```

> Los `.md` de la raíz son documentación **histórica / de negocio**. La fuente de verdad técnica es el código y esta carpeta `docs/`.

## 2. Backend (`backend/`)

```
backend/
├── app/
│   ├── main.py                 # Punto de entrada FastAPI: routers, CORS, lifespan, healthchecks
│   ├── config.py               # Settings (pydantic-settings) + feature flags
│   ├── database.py             # Engine async, sessionmaker, Base, get_db
│   ├── api/v1/                 # Routers REST + WebSocket
│   │   ├── auth.py             # signup/login/refresh/logout/me
│   │   ├── organizations.py    # org, cuentas, páginas, modelos IA, políticas
│   │   ├── campaigns.py        # snapshots, sync-trigger, sugerencia, creación (Saga), drafts, learning
│   │   ├── insights.py         # listado/dismiss/trigger de análisis
│   │   ├── actions.py          # listar/aprobar/rechazar/estado de acciones
│   │   ├── reports.py          # brief-data, PDF, kpis-summary, spend-history
│   │   ├── ai_usage.py         # consumo de tokens agregado
│   │   └── ws.py               # WebSocket + Redis listener (pubsub→WS)
│   ├── models/                 # 22 modelos SQLAlchemy (ver 07-modelos; 26 antes de M-1/M-3)
│   ├── schemas/                # Pydantic: schemas.py, campaign_schemas.py, response_schemas.py (M-4)
│   ├── services/               # Lógica de negocio (ver abajo)
│   │   ├── ai/                 # Motor de IA: engine, rules, statistics, thresholds, planner, executor, llm, token_tracking
│   │   ├── meta_ads/           # Integración Meta: sync, modifier, rate_limiter, exceptions
│   │   ├── report_builder.py   # Agregador del Master Brief (KPIs, campañas, segmentación…)
│   │   ├── report_cache.py     # Cache Redis del ReportBuilder
│   │   ├── campaign_*.py       # suggester, validator, targeting, risk, learning, outcome
│   │   ├── creative_builder.py # object_story_spec de Meta
│   │   ├── meta_asset_repository.py
│   │   ├── kpi_period.py, brand_code.py, policy_catalog.py
│   │   ├── pdf_report.py, snapshot_archiver.py, notification_service.py
│   ├── workers/                # Celery
│   │   ├── celery_app.py       # App Celery, colas, task_routes, beat_schedule
│   │   ├── tasks.py            # sync, análisis, ejecución, outcomes, archivado
│   │   └── creation_tasks.py   # Saga de creación (5 pasos + error handler)
│   ├── tenant_rules/           # campaign_defaults.py (reglas por tenant, hoy globales)
│   └── utils/                  # security.py (JWT/bcrypt), throttle.py (cooldowns)
├── alembic/                    # Migraciones (env.py async + versions/)
│   └── versions/               # 15 migraciones (cadena lineal)
├── tests/                      # 44 archivos de test (pytest async)
├── scripts/                    # seed_tenant.py, setup-hostgator.sh
├── media/                      # Imágenes subidas (bind mount en dev, volumen en prod)
├── Dockerfile                  # Multi-stage (builder + runtime), WeasyPrint deps
├── requirements.txt
├── pytest.ini / conftest.py    # Config de tests (SQLite async)
├── alembic.ini
└── run_tests.sh
```

### Organización de `app/services/` por dominio

| Subgrupo | Módulos | Doc |
|----------|---------|-----|
| **Motor de IA** | `ai/engine.py`, `ai/rules.py`, `ai/statistics.py`, `ai/thresholds.py`, `ai/planner.py`, `ai/executor.py`, `ai/llm.py`, `ai/token_tracking.py` | [18](18-motor-ia.md) |
| **Meta Ads** | `meta_ads/sync.py`, `meta_ads/modifier.py`, `meta_ads/rate_limiter.py`, `meta_ads/exceptions.py`, `meta_asset_repository.py` | [19](19-sincronizacion-meta.md) |
| **Creación de campañas** | `campaign_suggester.py`, `campaign_validator.py`, `campaign_targeting.py`, `campaign_risk.py`, `creative_builder.py`, `brand_code.py`, `tenant_rules/campaign_defaults.py` | [20](20-creacion-campanas.md) |
| **Aprendizaje** | `campaign_learning.py`, `campaign_outcome.py` | [18](18-motor-ia.md) |
| **Reporting** | `report_builder.py`, `report_cache.py`, `kpi_period.py`, `pdf_report.py`, `policy_catalog.py` | [12](12-flujos.md) |
| **Infra/soporte** | `notification_service.py`, `snapshot_archiver.py`, `utils/throttle.py`, `utils/security.py` | [03](03-backend.md) |

## 3. Frontend (`frontend/`)

```
frontend/
├── app/
│   ├── layout.tsx              # Root layout (fuentes, metadata)
│   ├── globals.css             # Estilos globales (tema oscuro, tokens CSS)
│   ├── landing/page.tsx        # Landing pública
│   ├── (auth)/login/page.tsx   # Login + signup
│   └── (dashboard)/            # Área autenticada (grupo de rutas)
│       ├── layout.tsx          # Sidebar, guard de auth, providers (WS, AdAccount)
│       ├── page.tsx            # Resumen (KPIs, gráficos)
│       ├── campaigns/          # listado, detalle [id], nueva, learning
│       ├── reporte/page.tsx    # Master Brief + PDF
│       ├── diagnostics/page.tsx# Insights de IA
│       ├── actions/page.tsx    # Acciones sugeridas (aprobar/rechazar)
│       ├── ai-usage/page.tsx   # Consumo de tokens
│       └── settings/page.tsx   # Configuración de org y cuentas
├── components/
│   ├── icons.tsx               # Iconos SVG inline
│   └── providers/              # AdAccountProvider, WebSocketProvider
├── lib/
│   ├── api.ts                  # Cliente HTTP (fetch + refresh + headers de tenant/cuenta)
│   ├── config.ts               # Resolución de API URL y tenant slug
│   ├── types.ts                # Alias sobre generated-api-types.ts (M-4)
│   ├── generated-api-types.ts  # Generado por `npm run generate-types` (openapi-typescript) — no editar a mano
│   ├── badge.ts                # Badges de estado/rendimiento
│   └── navigation.ts           # safeRedirect
├── middleware.ts               # Extrae subdominio → header x-tenant-slug
├── __tests__/                  # 4 tests (jest + testing-library)
├── Dockerfile                  # Multi-stage (deps/builder/runner standalone)
├── next.config.ts              # output: standalone
├── package.json, tsconfig.json, eslint.config.mjs, jest.config.ts
└── AGENTS.md / CLAUDE.md       # Notas para asistentes de IA
```

## 4. Infraestructura (`nginx/`)

```
nginx/
├── conf.d/
│   └── default.conf            # Local: *.localhost→frontend, api.localhost→backend
├── conf.d-tunnel/
│   └── tunnel.conf             # Single-host: rutea por path (/api, /ws, /)
└── ssl/                        # Certificados para el túnel local
```

> En **producción** no hay nginx propio: el proxy + TLS los pone **Coolify**. El stack de prod vive en [`docs/infra/coolify/lejovista.compose.yml`](infra/coolify/lejovista.compose.yml). Ver [14-deployment §3](14-deployment.md).

## 5. Archivos generados / que conviene ignorar

- `backend/.venv/`, `frontend/node_modules/`, `frontend/.next/`, `**/__pycache__/`, `backend/.pytest_cache/`, `frontend/.swc/` — artefactos de build/entorno.
- `backend/alembic_mock.db`, `frontend/tsconfig.tsbuildinfo` — caches locales.
- `.agents/skills/` y `.claude/skills/` (en ambos apps) — reglas para asistentes de IA, no forman parte del runtime.
- `informe-rendimiento-jun2026.html` — artefacto de ejemplo en la raíz.

## 6. Nota sobre archivos borrados en el working tree

El `git status` inicial muestra varios `.md` de planificación **borrados** (`status.md`, `plan-p2-reporting.md`, `paso0crearCampañas.md`, `paso1DescargarInfoApi.md`, `auditoria2026.md`) y un `frontend/lib/types.ts` **nuevo sin commitear**. Es señal de una limpieza de documentación en curso; no afecta al runtime.
