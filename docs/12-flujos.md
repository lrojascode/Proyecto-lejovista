# 12 · Flujos del Sistema

## 1. Ciclo de vida de una petición HTTP

```mermaid
sequenceDiagram
    participant B as Navegador
    participant N as Nginx
    participant F as FastAPI
    participant Dep as Dependencias
    participant S as Servicio
    participant DB as Postgres

    B->>N: GET https://cliente1.dominio/api/v1/insights (Bearer)
    N->>F: proxy + X-Tenant-Slug: cliente1
    F->>F: CORS + tenant_slug_middleware (log)
    F->>Dep: get_current_user (valida JWT + org activa S-8)
    F->>Dep: get_active_ad_account_id (valida cuenta)
    F->>Dep: get_db (sesión async)
    F->>S: consulta filtrada por org + cuenta
    S->>DB: SELECT … WHERE organization_id=… AND is_dismissed=false
    DB-->>S: filas
    S-->>F: modelos
    F-->>B: JSON (commit implícito en get_db)
```

## 2. Sincronización con Meta

Disparadores: conexión de cuenta (`FULL`), trigger manual (`INCREMENTAL`, cooldown 60 s), Beat diario 06:00 (`INCREMENTAL`, escalonado con jitter).

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant Q as Redis/Celery (cola sync)
    participant W as worker sync
    participant M as Meta API
    participant DB as Postgres
    participant WS as WebSocket

    API->>Q: sync_ad_account.delay(acc, tipo)
    Q->>W: ejecuta run_sync
    W->>DB: SyncJob RUNNING
    alt MOCK_SERVICES
        W->>DB: genera datos mock (3 campañas, 30d en FULL)
    else real
        W->>M: cuenta → campañas → insights (campaign/adset/ad)
        W->>M: breakdowns (placement/edad/género/device/región)
        W->>M: inventario (adimages, customaudiences, páginas+WhatsApp)
        W->>M: histórico mensual de gasto (backfill único)
        W-->>W: tras FULL, encola BACKFILL diario separado (180 días)
        W->>DB: upsert idempotente de snapshots
    end
    W->>DB: SyncJob COMPLETED
    W->>WS: notifica sync_status
    W->>Q: invalida cache de reporte + agenda análisis (debounce D-1)
```

Reintentos automáticos ante `TransientMetaError` (backoff exponencial, `max_retries=5`, `acks_late=True`). Detalle en [19](19-sincronizacion-meta.md).

## 3. Análisis de IA (3 capas)

Disparadores: post-sync con debounce (5 min tras el 1er sync de la org), trigger manual (cooldown 120 s), Beat 07:00.

```mermaid
flowchart TB
    S["Snapshots (DB)"] --> C1["Capa 1: Reglas\n(rules.py)\nCPL crítico, saturación,\nCTR bajo, 0 conv, placements"]
    S --> C2["Capa 2: Estadística\n(statistics.py)\ntendencia CPL, Z-score,\naviso de histórico insuficiente"]
    C1 --> INS["AIInsight (dedupe: reemplaza foto anterior, B-1)"]
    C2 --> INS
    INS --> C3["Capa 3: LLM (llm.py)\nnarrativa Master Brief v2\n(o fallback local)"]
    C3 --> NAR["AdAccount.cached_narrative + metadatos"]
    INS --> PL["AIPlanner (planner.py)\nInsight → AIAction PROPOSED"]
    PL --> ACT["AIAction (evita duplicados pendientes)"]
    C3 --> WS["Notifica WebSocket analysis_status"]
```

Detalle de umbrales, planner y contexto enriquecido en [18](18-motor-ia.md).

## 4. Aprobación y ejecución de una acción

```mermaid
sequenceDiagram
    participant U as Usuario
    participant API as /actions
    participant Q as Celery (cola ai)
    participant E as AIExecutor
    participant Mod as MetaCampaignModifier
    participant M as Meta

    U->>API: POST /actions/{id}/approve {approve:true}
    API->>API: status = APPROVED
    API->>Q: execute_action_task.delay(id)
    Q->>E: execute_action
    alt action = CREATE_CAMPAIGN
        E->>E: despacha Saga de creación (draft + chain)
        Note over E: si falta página/creativo sin ambigüedad → NEEDS_INPUT
    else modificación
        E->>Mod: modify (backup_state + reglas de seguridad)
        Mod->>M: POST (real o simulado)
        alt error con backup
            E->>Mod: rollback automático
        end
        E->>E: status COMPLETED/FAILED (marca simulated)
    end
```

## 5. Creación de campaña (Saga)

Ver el flujo completo, con los 5 pasos del `chain` y el manejo de errores, en [20-creacion-campanas](20-creacion-campanas.md).

## 6. Aprendizaje (outcome loop)

```mermaid
flowchart LR
    CR["Creación → CampaignLearningLog CREATED\n(qué propuso la IA + expectativa)"] --> W["Ventana de evaluación (7d)"]
    W --> BEAT["Beat 08:00: evaluate_campaign_outcomes"]
    BEAT --> AGG["Agrega snapshots reales de la campaña"]
    AGG --> V["compute_verdict:\nWIN / LOSS / NEUTRAL / NOT_ACTIVATED"]
    V --> LOG["outcome_json + status EVALUATED"]
    LOG --> MAT["assess_maturity: la próxima sugerencia\npesa los resultados propios"]
```

Los WIN/LOSS propios influyen en si la IA declara "convergencia" (modo optimizado) o sigue explorando. Detalle en [18](18-motor-ia.md).

## 7. Generación de reportes

- **En pantalla**: `GET /reports/brief-data` → `ReportBuilder` (cacheado en Redis) arma KPIs, ranking, adsets, ads, segmentación, audiencias, creativos, alertas, parámetros óptimos, hallazgos y recomendaciones + narrativa cacheada + comparativa Δ% (7/30/90 días).
- **PDF**: `GET /reports/pdf` → `pdf_report.py` renderiza los **mismos datos** con Jinja2 + WeasyPrint (sin recalcular ni inventar cifras).
- **Dashboard**: `GET /reports/kpis-summary` → resumen liviano (KPIs 7d + Δ%, alertas críticas, último sync).

## 8. Tareas programadas (Celery Beat, hora Lima)

| Hora | Tarea | Efecto |
|------|-------|--------|
| 06:00 diario | `schedule_all_syncs("INCREMENTAL")` | Sync de todas las cuentas activas (escalonado) |
| 07:00 diario | `schedule_all_analyses` | Análisis de todas las orgs activas |
| 08:00 diario | `evaluate_campaign_outcomes` | Cierra el ciclo de aprendizaje |
| Domingo 03:00 | `archive_old_snapshots_task` | Archiva snapshots > 180 días |

## 9. Notificaciones en tiempo real

```mermaid
flowchart LR
    W["Worker (sync/ai/creation)"] -->|publish| R[(Redis canal ws_notifications)]
    R -->|redis_listener| API["FastAPI (tarea de fondo)"]
    API -->|broadcast_to_tenant| WS["WebSocket del tenant"]
    WS --> FE["Frontend (lastEvent → refresca UI)"]
```

`NotificationService.notify_job_status` (síncrono, desde Celery) publica; el `redis_listener` (async, en el lifespan de FastAPI) retransmite al `ConnectionManager` que hace broadcast por `tenant_id`.
