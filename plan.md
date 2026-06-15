# Plan de Producto — LejoVista

## Plataforma de Análisis, Optimización y Gestión Autónoma de Campañas con IA

---

## Resumen Ejecutivo

Plataforma multi-tenant que **descarga datos de campañas, los analiza con IA, implementa mejoras automáticamente y crea nuevas campañas** basándose en el aprendizaje continuo. Cada cliente-empresa tiene su propio dashboard.

| Decisión | Valor |
|---|---|
| Nombre | **LejoVista** |
| Stack | Next.js + Python/FastAPI + PostgreSQL |
| IA | OpenRouter (Gemini, Claude, modelo variable) |
| Hosting | VPS HostGator (acceso root) |
| Dominio | `lrojas.dev` (fácil de cambiar después) |
| DNS actual | name.com (migración a Cloudflare en ~3 meses) |
| Clientes iniciales | 1 |
| Cobro | Por contrato individual |
| Meta App | ✅ Ya existe |
| **Alcance IA** | **Análisis + Modificación de campañas + Creación de campañas** |

---

## Alcance Completo de la IA

```mermaid
graph TB
    subgraph "PASO 1 — Recolección (paso1DescargarInfoApi.md)"
        A["Sync automático<br/>15 queries Meta API"] --> B["Almacenar en PostgreSQL"]
    end

    subgraph "PASO 2 — Análisis (Motor IA)"
        B --> C["Capa 1: Reglas fijas"]
        C --> D["Capa 2: Estadística"]
        D --> E["Capa 3: LLM (OpenRouter)"]
        E --> F["Diagnóstico completo<br/>Campañas ✅⚠️❌"]
    end

    subgraph "PASO 3 — Acción (paso0crearCampañas.md)"
        F --> G{"¿Qué hacer?"}
        G -->|"Campaña fallida"| H["Proponer MODIFICACIÓN<br/>budget, targeting, placements"]
        G -->|"Oportunidad detectada"| I["Proponer NUEVA CAMPAÑA<br/>basada en patrones ganadores"]
        G -->|"Campaña saturada"| J["Proponer PAUSAR<br/>+ redistribuir budget"]
        
        H & I & J --> K["Cola de acciones pendientes"]
        K --> L{"¿Modo?"}
        L -->|"Semi-auto"| M["Esperar aprobación<br/>humana en dashboard"]
        L -->|"Full-auto"| N["Ejecutar con reglas<br/>de seguridad"]
        M & N --> O["Ejecutar vía Meta API<br/>(con pausas anti-detección)"]
    end

    style F fill:#7c3aed,color:#fff
    style O fill:#2563eb,color:#fff
```

### Tres modos de operación de la IA:

| Modo | Descripción | Cuándo usar |
|---|---|---|
| 🔵 **Solo análisis** | La IA analiza y reporta, no toca nada | Primeras semanas, clientes cautelosos |
| 🟡 **Semi-autónomo** | La IA propone acciones, tú apruebas en el dashboard | Fase de confianza, recomendado inicialmente |
| 🟢 **Full-autónomo** | La IA ejecuta dentro de reglas de seguridad (budget caps, etc.) | Cuando ya confías en las decisiones |

> **Recomendación**: Empezar todos los clientes en modo **🟡 semi-autónomo** las primeras 4 semanas. Pasar a full-auto solo después de validar que las sugerencias son buenas.

---

## Decisiones Técnicas Confirmadas

| Pregunta | Respuesta |
|---|---|
| VPS | HostGator VPS (Linux, acceso root para Docker) |
| DNS | name.com actualmente → migración a Cloudflare en ~3 meses |
| SSL | Certbot manual (DNS challenge con TXT record en name.com). Después de migrar a Cloudflare: automático con plugin `--dns-cloudflare` |
| Dominio | `lrojas.dev` configurable desde `.env` para cambiar después |
| Clientes | 1 inicial — VPS dimensionado mínimo |
| Meta App | Ya existe, lista para OAuth |

---

## COMPONENTE 1: Estructura del Proyecto

### Estructura de directorios

```
lejovista/
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── .env                             # NUNCA en git
├── nginx/
│   ├── conf.d/
│   │   └── default.conf             # Wildcard subdominios *.lrojas.dev
│   └── ssl/
│
├── backend/                         # Python / FastAPI
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── app/
│   │   ├── main.py                  # FastAPI app + CORS + middleware
│   │   ├── config.py                # Settings (Pydantic BaseSettings)
│   │   ├── database.py              # SQLAlchemy async engine
│   │   │
│   │   ├── models/                  # SQLAlchemy ORM
│   │   │   ├── __init__.py
│   │   │   ├── organization.py      # Tenant (cliente)
│   │   │   ├── user.py              # Usuarios del tenant
│   │   │   ├── ad_account.py        # Cuentas Meta/Google conectadas
│   │   │   ├── campaign_snapshot.py # Métricas históricas
│   │   │   ├── ai_insight.py        # Insights generados por IA
│   │   │   ├── ai_action.py         # ★ Acciones propuestas/ejecutadas
│   │   │   ├── sync_job.py          # Jobs de sincronización
│   │   │   └── api_call_log.py      # Auditoría de API calls
│   │   │
│   │   ├── schemas/                 # Pydantic (request/response)
│   │   │   ├── auth.py
│   │   │   ├── campaigns.py
│   │   │   ├── insights.py
│   │   │   └── actions.py           # ★ Esquemas de acciones IA
│   │   │
│   │   ├── api/v1/                  # Rutas API
│   │   │   ├── auth.py
│   │   │   ├── organizations.py
│   │   │   ├── accounts.py
│   │   │   ├── campaigns.py
│   │   │   ├── insights.py
│   │   │   ├── actions.py           # ★ Aprobar/rechazar acciones IA
│   │   │   ├── reports.py
│   │   │   └── admin.py
│   │   │
│   │   ├── services/
│   │   │   ├── meta_ads/
│   │   │   │   ├── auth.py          # OAuth 2.0
│   │   │   │   ├── sync.py          # Descarga datos (paso1)
│   │   │   │   ├── campaigns.py     # Crear campañas (paso0)
│   │   │   │   ├── modifier.py      # ★ Modificar campañas existentes
│   │   │   │   └── rate_limiter.py  # Pausas anti-detección
│   │   │   ├── google_ads/
│   │   │   │   ├── auth.py
│   │   │   │   ├── sync.py
│   │   │   │   ├── campaigns.py
│   │   │   │   └── modifier.py      # ★ Modificar campañas Google
│   │   │   ├── ai/
│   │   │   │   ├── engine.py        # Motor principal (3 capas)
│   │   │   │   ├── rules.py         # Capa 1: Reglas
│   │   │   │   ├── statistics.py    # Capa 2: Estadística
│   │   │   │   ├── llm.py           # Capa 3: OpenRouter
│   │   │   │   ├── planner.py       # ★ Planifica acciones
│   │   │   │   ├── executor.py      # ★ Ejecuta acciones aprobadas
│   │   │   │   └── prompts.py       # Prompts del sistema
│   │   │   ├── encryption.py
│   │   │   └── notifications.py
│   │   │
│   │   ├── workers/                 # Celery tasks
│   │   │   ├── sync_tasks.py        # Descarga programada
│   │   │   ├── ai_tasks.py          # Análisis + planificación
│   │   │   ├── action_tasks.py      # ★ Ejecución de acciones
│   │   │   └── maintenance.py       # Backups, limpieza
│   │   │
│   │   └── utils/
│   │       ├── security.py
│   │       └── domain.py            # ★ Resolución de dominio configurable
│   │
│   └── tests/
│
├── frontend/                        # Next.js
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   ├── middleware.ts                 # Resolución de tenant por subdominio
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── callback/page.tsx
│   │   ├── (dashboard)/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx             # Dashboard principal
│   │   │   ├── campaigns/
│   │   │   │   ├── page.tsx         # Lista campañas
│   │   │   │   └── [id]/page.tsx    # Detalle + métricas diarias
│   │   │   ├── diagnostics/
│   │   │   │   └── page.tsx         # Diagnósticos IA ✅⚠️❌
│   │   │   ├── actions/
│   │   │   │   └── page.tsx         # ★ Cola de acciones pendientes
│   │   │   ├── reports/
│   │   │   │   └── page.tsx
│   │   │   ├── accounts/
│   │   │   │   └── page.tsx
│   │   │   └── settings/
│   │   │       └── page.tsx
│   │   └── (admin)/
│   │       ├── clients/page.tsx
│   │       └── system/page.tsx
│   ├── components/
│   ├── lib/
│   │   ├── api.ts
│   │   └── config.ts               # ★ Dominio configurable
│   └── styles/
│       └── globals.css
│
└── scripts/
    ├── setup-hostgator.sh           # Setup VPS HostGator
    ├── backup.sh
    └── deploy.sh
```

> Los archivos marcados con ★ son los relacionados con la autonomía de la IA.

---

## COMPONENTE 2: Dominio Configurable

### `backend/app/utils/domain.py`

```python
"""
Resolución de dominio configurable.
Permite cambiar de lrojas.dev a lejovista.com sin tocar código.
"""

# .env
# BASE_DOMAIN=lrojas.dev
# PROTOCOL=https

# Uso:
# cliente1.lrojas.dev  → ahora
# cliente1.lejovista.com → después (solo cambiar .env + DNS)
```

### `frontend/lib/config.ts`

```typescript
/**
 * Dominio base configurable desde variable de entorno.
 * Cambiar NEXT_PUBLIC_BASE_DOMAIN en .env para migrar de dominio.
 */
export const BASE_DOMAIN = process.env.NEXT_PUBLIC_BASE_DOMAIN || "lrojas.dev";
// Ahora: cliente1.lrojas.dev
// Después: cliente1.lejovista.com (solo cambiar env + DNS + SSL)
```

**Para migrar de dominio después:**
1. Cambiar `BASE_DOMAIN` en `.env`
2. Actualizar DNS (A record + wildcard) en el nuevo registrar
3. Renovar certificado SSL con Certbot
4. `docker compose restart`

---

## COMPONENTE 3: Sistema de Acciones de la IA

Este es el corazón — la IA no solo analiza, sino que **planifica y ejecuta acciones**.

---

### 3.1 Modelo de Datos para Acciones

#### `backend/app/models/ai_action.py`

```python
"""
Acción propuesta o ejecutada por la IA.
Cada acción pasa por un ciclo de vida:
  PROPOSED → APPROVED → EXECUTING → COMPLETED
  PROPOSED → REJECTED
  PROPOSED → APPROVED → EXECUTING → FAILED
"""

class AIAction(Base):
    __tablename__ = "ai_actions"

    id: str                     # CUID
    insight_id: str             # FK → AIInsight que originó la acción

    # Tipo de acción
    action_type: ActionType
    # MODIFY_BUDGET      → Cambiar presupuesto de campaña/adset
    # MODIFY_TARGETING   → Cambiar segmentación (edad, geo, etc.)
    # MODIFY_PLACEMENTS  → Cambiar placements (agregar/quitar reels, etc.)
    # MODIFY_SCHEDULE    → Cambiar fechas/horarios
    # PAUSE_CAMPAIGN     → Pausar campaña (CPL alto, frequency alta)
    # RESUME_CAMPAIGN    → Reactivar campaña pausada
    # CREATE_CAMPAIGN    → Crear campaña nueva completa
    # DUPLICATE_CAMPAIGN → Duplicar campaña exitosa con variaciones

    # Nivel de riesgo (herencia del paso0)
    risk_level: RiskLevel
    # LOW    → 🟢 Lectura, cambios menores (schedule, placements)
    # MEDIUM → 🟡 Escritura simple (budget, targeting)
    # HIGH   → 🔴 Escritura crítica (crear campaña, pausar activa)

    # Qué se va a hacer (legible para humanos)
    title: str
    description: str            # Explicación detallada de por qué
    
    # Datos técnicos de la acción
    target_campaign_id: str     # Campaign ID en Meta/Google (si aplica)
    target_adset_id: str        # Ad Set ID (si aplica)
    platform: Platform          # META | GOOGLE

    # Parámetros exactos del cambio (JSON)
    # Para MODIFY_BUDGET:    {"old_budget": 50000, "new_budget": 75000}
    # Para MODIFY_TARGETING: {"field": "age_max", "old": 65, "new": 55}
    # Para CREATE_CAMPAIGN:  {todo el payload del paso0, 6 pasos}
    parameters: dict

    # API calls que se van a ejecutar
    # [{"method": "POST", "endpoint": "act_XXX/campaigns", "body": {...}}]
    planned_api_calls: list[dict]

    # Ciclo de vida
    status: ActionStatus        # PROPOSED | APPROVED | REJECTED | EXECUTING | COMPLETED | FAILED
    proposed_at: datetime
    approved_at: datetime
    approved_by: str            # User ID o "AUTO" si es full-auto
    rejected_at: datetime
    reject_reason: str
    executed_at: datetime
    execution_result: dict      # IDs creados, confirmaciones, errores

    # Impacto estimado
    estimated_impact: str       # "CPL podría bajar de S/3.50 a S/2.00"
    confidence: float           # 0.0 - 1.0

    organization_id: str        # FK
```

#### Enums:

```python
class ActionType(str, Enum):
    MODIFY_BUDGET = "MODIFY_BUDGET"
    MODIFY_TARGETING = "MODIFY_TARGETING"
    MODIFY_PLACEMENTS = "MODIFY_PLACEMENTS"
    MODIFY_SCHEDULE = "MODIFY_SCHEDULE"
    PAUSE_CAMPAIGN = "PAUSE_CAMPAIGN"
    RESUME_CAMPAIGN = "RESUME_CAMPAIGN"
    CREATE_CAMPAIGN = "CREATE_CAMPAIGN"
    DUPLICATE_CAMPAIGN = "DUPLICATE_CAMPAIGN"

class RiskLevel(str, Enum):
    LOW = "LOW"       # 🟢
    MEDIUM = "MEDIUM" # 🟡
    HIGH = "HIGH"     # 🔴

class ActionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
```

---

### 3.2 Motor de Planificación de la IA

#### `backend/app/services/ai/planner.py`

```python
"""
El Planner toma los insights del análisis y genera ACCIONES concretas.

Flujo:
  AIEngine.run_analysis() → [AIInsight, ...]
       ↓
  AIPlanner.plan_actions(insights) → [AIAction, ...]
       ↓
  Cola de acciones pendientes (dashboard)
       ↓
  Aprobación humana (o auto-aprobación si full-auto)
       ↓
  AIExecutor.execute(action) → resultado
"""

class AIPlanner:

    # ══════════════════════════════════════
    # REGLAS DE PLANIFICACIÓN
    # (extraídas de paso0crearCampañas.md)
    # ══════════════════════════════════════

    PLANNING_RULES = {
        # Si CPL > 2x objetivo → pausar campaña + redistribuir
        "high_cpl": {
            "condition": "cpl > target_cpl * 2",
            "action": ActionType.PAUSE_CAMPAIGN,
            "risk": RiskLevel.HIGH,
        },
        # Si frequency > 3.0 → audiencia saturada → ampliar targeting
        "high_frequency": {
            "condition": "frequency > 3.0",
            "action": ActionType.MODIFY_TARGETING,
            "risk": RiskLevel.MEDIUM,
        },
        # Si CTR < 0.5% → creatividad débil → sugerir nueva campaña
        "low_ctr": {
            "condition": "ctr < 0.005",
            "action": ActionType.CREATE_CAMPAIGN,
            "risk": RiskLevel.HIGH,
        },
        # Si Reels CPL < Feed CPL → mover más budget a Reels
        "reels_opportunity": {
            "condition": "reels_cpl < feed_cpl * 0.7",
            "action": ActionType.MODIFY_PLACEMENTS,
            "risk": RiskLevel.LOW,
        },
        # Si Instagram CPL > S/5 → quitar Instagram
        "instagram_waste": {
            "condition": "instagram_cpl > 5.0",
            "action": ActionType.MODIFY_PLACEMENTS,
            "risk": RiskLevel.LOW,
        },
        # Si campaña exitosa (CPL < objetivo) → duplicar con variación
        "successful_campaign": {
            "condition": "cpl < target_cpl * 0.8 and days_running > 7",
            "action": ActionType.DUPLICATE_CAMPAIGN,
            "risk": RiskLevel.HIGH,
        },
        # Si spend_cap casi agotado → ALERTA, no crear nada
        "spend_cap_alert": {
            "condition": "spend_cap - amount_spent < budget_needed",
            "action": None,  # Solo alerta, no acción
            "risk": RiskLevel.HIGH,
        },
    }

    async def plan_actions(
        self, org_id: str, insights: list[AIInsight]
    ) -> list[AIAction]:
        """
        Convierte insights en acciones ejecutables.
        
        Para cada insight:
        1. Determinar qué acción tomar (reglas + LLM)
        2. Calcular parámetros exactos del cambio
        3. Generar las API calls planificadas
        4. Estimar impacto y confianza
        5. Crear AIAction en estado PROPOSED
        """

    async def _plan_campaign_creation(
        self, org_id: str, insight: AIInsight
    ) -> AIAction:
        """
        Genera una campaña completa basada en patrones ganadores.
        
        Usa el formato exacto del paso0:
        - Nombre: CLIENTE_MSJ_[PRODUCTO][ZONA][MES][AÑO]
        - Objetivo: OUTCOME_LEADS
        - Buying type: AUCTION
        - Status: PAUSED (siempre)
        - Targeting: Lima Región key:2771, edad 25-65
        - Placements: feed + story + facebook_reels (nunca Instagram)
        - WhatsApp: WHATSAPP_MESSAGE con page_welcome_message
        
        Los 6 API calls se planifican pero NO se ejecutan
        hasta que alguien apruebe.
        """

    async def _plan_modification(
        self, campaign_id: str, modification: dict
    ) -> AIAction:
        """
        Genera una modificación a campaña existente.
        
        Tipos de modificación soportados:
        - Budget: POST /{adset_id} con nuevo lifetime_budget
        - Targeting: POST /{adset_id} con nuevo targeting JSON
        - Placements: POST /{adset_id} con nuevos publisher_platforms
        - Status: POST /{campaign_id} con status PAUSED/ACTIVE
        """
```

---

### 3.3 Ejecutor de Acciones

#### `backend/app/services/ai/executor.py`

```python
"""
Ejecuta acciones aprobadas contra Meta/Google API.
Hereda TODAS las reglas de seguridad del paso0.
"""

class AIExecutor:

    # ══════════════════════════════════════
    # REGLAS DE SEGURIDAD (del paso0)
    # ══════════════════════════════════════
    # ❌ Máximo 3 POSTs por sesión
    # ❌ Pausa 30-50 segundos entre POSTs
    # ❌ Si hay error en cualquier POST: pausar TODA la sesión
    # ❌ Nunca crear campaña en estado ACTIVE (siempre PAUSED)
    # ❌ Nunca aumentar presupuesto sin aprobación
    # ❌ Nunca duplicar adsets/ads sin confirmación
    # ❌ Nunca cambiar estado de campañas que ya existían
    # ❌ Máximo 3 campañas nuevas por sesión

    # REGLAS ADICIONALES PARA IA AUTÓNOMA
    # ❌ Budget máximo por acción: 150% del budget actual
    # ❌ No puede eliminar campañas (solo pausar)
    # ❌ No puede modificar más de 2 campañas por hora
    # ❌ Cada acción se loguea con todos los detalles
    # ❌ Rollback automático si CPL sube >50% en 24h post-cambio

    async def execute(self, action: AIAction) -> ExecutionResult:
        """
        Ejecuta una acción aprobada.
        
        1. Verificar que la acción está en estado APPROVED
        2. Verificar que no se exceden límites de sesión
        3. Descifrar token de la cuenta
        4. Ejecutar API calls con pausas anti-detección
        5. Loguear cada call en api_call_logs
        6. Actualizar estado de la acción
        7. Programar verificación de impacto (24h después)
        """

    async def execute_campaign_creation(self, action: AIAction) -> dict:
        """
        Ejecuta los 6 pasos del paso0 para crear campaña.
        
        Paso 1: GET  → Verificar cuenta (spend_cap, status)
          pausa 30-50s
        Paso 2: POST → Subir imagen (si es nueva)
          pausa 30-50s
        Paso 3: POST → Crear Campaign (PAUSED)
          pausa 30-50s
        Paso 4: POST → Crear Ad Set (targeting ganador)
          pausa 30-50s
        Paso 5: POST → Crear Creative (copy + welcome message)
          pausa 30-50s
        Paso 6: POST → Crear Ad
        
        → Generar reporte final con todos los IDs
        → Guardar en log de campañas creadas
        """

    async def execute_modification(self, action: AIAction) -> dict:
        """
        Ejecuta modificación de campaña existente.
        
        1. GET actual state (para backup/rollback)
        2. Guardar snapshot pre-cambio
        3. POST con los nuevos parámetros
        4. Verificar que el cambio se aplicó
        5. Programar monitoreo de impacto
        """

    async def _verify_impact(self, action: AIAction):
        """
        Se ejecuta 24h después de una acción.
        Compara métricas antes vs después.
        Si CPL subió >50% → crear alerta + proponer rollback.
        """
```

---

### 3.4 Flujo Completo: Del Análisis a la Ejecución

```mermaid
sequenceDiagram
    participant CRON as Celery Beat
    participant SYNC as Sync Worker
    participant META as Meta API
    participant DB as PostgreSQL
    participant AI as AI Engine
    participant LLM as OpenRouter
    participant PLAN as AI Planner
    participant DASH as Dashboard
    participant USER as Usuario
    participant EXEC as AI Executor

    Note over CRON: 7:00 AM — Sync diario
    CRON->>SYNC: Trigger sync:incremental
    SYNC->>META: 4 queries (con pausas 12-22s)
    META-->>SYNC: Métricas del día anterior
    SYNC->>DB: Guardar campaign_snapshots

    Note over CRON: 8:00 AM — Análisis IA
    CRON->>AI: Trigger ai:analyze
    AI->>DB: Leer últimos 30 días de data
    AI->>AI: Capa 1: Reglas fijas
    AI->>AI: Capa 2: Estadística
    AI->>LLM: Capa 3: Análisis con contexto
    LLM-->>AI: Diagnóstico en lenguaje natural
    AI->>DB: Guardar AIInsights

    Note over PLAN: 8:30 AM — Planificación
    AI->>PLAN: Insights detectados
    PLAN->>PLAN: Convertir insights → acciones
    PLAN->>DB: Guardar AIActions (PROPOSED)
    PLAN->>DASH: Notificar: "3 acciones pendientes"

    Note over USER: 9:00 AM — Revisión
    USER->>DASH: Ver acciones pendientes
    DASH->>DB: Listar AIActions (PROPOSED)
    DASH-->>USER: Mostrar acciones con riesgo 🟢🟡🔴

    USER->>DASH: Aprobar acción #1 (MODIFY_PLACEMENTS 🟢)
    DASH->>DB: Action #1 → APPROVED
    USER->>DASH: Aprobar acción #2 (CREATE_CAMPAIGN 🔴)
    DASH->>DB: Action #2 → APPROVED
    USER->>DASH: Rechazar acción #3
    DASH->>DB: Action #3 → REJECTED

    Note over EXEC: Ejecución inmediata
    DB->>EXEC: Acciones aprobadas
    EXEC->>META: POST modificación (pausa 30-50s)
    META-->>EXEC: OK ✅
    EXEC->>DB: Action #1 → COMPLETED
    EXEC->>META: 6 POSTs crear campaña (pausas entre cada uno)
    META-->>EXEC: IDs creados
    EXEC->>DB: Action #2 → COMPLETED

    Note over EXEC: 24h después — verificar impacto
    EXEC->>DB: Comparar métricas antes vs después
    EXEC->>AI: ¿Mejoró o empeoró?
```

---

### 3.5 Vista del Dashboard — Cola de Acciones

```
┌─────────────────────────────────────────────────────────────────────┐
│  🤖 Acciones Propuestas por IA                    3 pendientes     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  🟢 BAJA — Optimizar placements                                   │
│  ─────────────────────────────────────────────────────────────────  │
│  Campaña: MTV_MSJ_CABLE_GPT_CERCADO_JUN2026                       │
│  Acción: Quitar Instagram (CPL S/6.80) → solo Feed+Story+Reels    │
│  Impacto estimado: CPL baja de S/3.50 a S/2.80 (-20%)             │
│  Confianza: 87%                                                     │
│  [✅ Aprobar]  [❌ Rechazar]  [👁 Ver detalles]                    │
│                                                                     │
│  🟡 MEDIA — Ajustar presupuesto                                   │
│  ─────────────────────────────────────────────────────────────────  │
│  Campaña: MTV_MSJ_VULCANIZADO_SURQUILLO_JUN2026                   │
│  Acción: Subir budget de S/50 → S/75 (campaña con CPL S/1.20)     │
│  Impacto estimado: 50% más leads al mismo CPL                      │
│  Confianza: 74%                                                     │
│  [✅ Aprobar]  [❌ Rechazar]  [👁 Ver detalles]                    │
│                                                                     │
│  🔴 ALTA — Crear campaña nueva                                    │
│  ─────────────────────────────────────────────────────────────────  │
│  Basada en: Patrón ganador de Cable GPT (CPL S/1.48 en Reels)     │
│  Producto: THHN (no tiene campaña activa)                           │
│  Zona: Cercado | WhatsApp: 51XXXXXXXXX                             │
│  Budget: S/70 × 7 días | Placements: Feed+Story+Reels             │
│  Confianza: 68%                                                     │
│  [✅ Aprobar]  [❌ Rechazar]  [👁 Ver campaña completa]            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## COMPONENTE 4: Servicio de Modificación de Campañas

### `backend/app/services/meta_ads/modifier.py`

```python
"""
Modificador de campañas existentes vía Meta API.
Operaciones de escritura con todas las salvaguardas del paso0.
"""

class MetaCampaignModifier:
    """
    Operaciones soportadas:
    
    1. Modificar presupuesto (Ad Set level)
       POST /{adset_id}
       body: {"lifetime_budget": nuevo_valor_en_centavos}
       Restricción: máximo 150% del budget actual
    
    2. Modificar targeting (Ad Set level)
       POST /{adset_id}
       body: {"targeting": {nuevo_targeting}}
       Restricción: siempre mantener geo_locations Lima (key:2771)
    
    3. Modificar placements (Ad Set level)
       POST /{adset_id}
       body: {"targeting": {"publisher_platforms": [...], ...}}
       Restricción: siempre incluir facebook_reels
    
    4. Pausar campaña
       POST /{campaign_id}
       body: {"status": "PAUSED"}
       Sin restricción (pausar siempre es seguro)
    
    5. Reactivar campaña
       POST /{campaign_id}
       body: {"status": "ACTIVE"}
       Restricción: requiere aprobación explícita (HIGH risk)
    
    6. Modificar schedule (Ad Set level)
       POST /{adset_id}
       body: {"end_time": "nueva_fecha"}
    
    ANTES de cada modificación:
    - GET estado actual → guardar snapshot para rollback
    - Verificar spend_cap
    - Verificar que la campaña existe y está activa
    """

    async def modify(
        self, account: AdAccount, action: AIAction
    ) -> ModificationResult:
        """Ejecuta una modificación con todas las salvaguardas."""

    async def rollback(self, action: AIAction) -> ModificationResult:
        """Revierte una modificación usando el snapshot pre-cambio."""
```

---

## COMPONENTE 5: Servicio de Sincronización (paso1 automatizado)

### `backend/app/services/meta_ads/sync.py`

```python
"""
Servicio de sincronización Meta Ads.
Traduce las 15 queries del paso1DescargarInfoApi.md a un proceso automatizado
con las mismas reglas de seguridad anti-detección.
"""

class MetaAdsSyncService:
    """
    Ejecuta las queries del paso1 respetando:
    - Pausas variables (random entre min/max del AdAccount)
    - Máximo 15 GETs por sesión
    - Pausa larga cada 3 llamadas
    - Headers que imitan navegador
    - Sin reintentos agresivos (espera 60s si falla)
    - Solo lectura (GET) durante sync
    """

    async def full_sync(self, account: AdAccount) -> SyncJob:
        """Sync completo inicial — ejecuta las 15 queries."""
        # Query 1:  Info de la cuenta (spend_cap, status)       → CRÍTICA
        # Query 2:  Campañas con insights completos              → ALTA
        # Query 3:  Breakdown edad/género                        → MEDIA
        # Query 4:  Breakdown plataforma/placement               → MEDIA
        # Query 5:  Breakdown dispositivo                        → BAJA
        # Query 6:  Estructura adsets (targeting, budget)         → ALTA
        # Query 7:  Imágenes en la cuenta (hash, url)            → BAJA
        # Query 8:  Audiencias personalizadas                    → MEDIA
        # Query 9:  Tendencia semanal 90 días                   → MEDIA
        # Query 10: Datos diarios último mes                     → ALTA
        # Query 11: Breakdown regional                           → MEDIA
        # Query 12: Info de la página                            → BAJA
        # Query 13: Lista campañas con IDs y fechas              → ALTA
        # Query 14: [Opcional] Breakdown hora del día            → BAJA
        # Query 15: [Opcional] Breakdown día de semana           → BAJA

    async def incremental_sync(self, account: AdAccount) -> SyncJob:
        """Sync diario — solo queries 1, 2, 6, 10 (las de prioridad ALTA)."""
```

**Frecuencias de sincronización automática (Celery Beat):**

| Job | Frecuencia | Queries | Descripción |
|---|---|---|---|
| `sync:full` | Al conectar cuenta | 15 queries | Descarga todo el historial |
| `sync:incremental` | Diario 7:00 AM | 4 queries | Solo métricas del día anterior |
| `sync:weekly` | Lunes 6:00 AM | 11 queries | Breakdowns completos |
| `token:refresh` | Cada 50 min | 1 query | Renueva access token |

---

## COMPONENTE 6: Motor de IA — 3 Capas

### `backend/app/services/ai/engine.py`

```python
"""
Motor de IA de LejoVista — analiza datos de campañas en 3 capas.
"""

class AIEngine:
    """
    CAPA 1 — REGLAS (Inmediata, sin LLM, sin costo)
    ─────────────────────────────────────────────────
    Reglas extraídas de los briefs existentes:
    - spend_cap agotado → ALERTA CRÍTICA
    - account_status ≠ 1 → ALERTA CRÍTICA
    - CPL > 2x objetivo → campaña FALLIDA
    - Frequency > 3.0 → audiencia SATURADA
    - CTR < 0.5% → creatividad DÉBIL
    - 0 conversiones en 48h → configuración ERRÓNEA
    - Instagram placements con CPL > S/5 → INEFICIENTE
    - Reels placement CPL < feed → OPORTUNIDAD

    CAPA 2 — ESTADÍSTICA (Requiere 1-2 semanas de data)
    ─────────────────────────────────────────────────
    - Media móvil de CPL (7 días, 14 días, 30 días)
    - Detección de tendencias (pendiente de la regresión)
    - Correlaciones: placement ↔ CPL, hora ↔ conversiones
    - Detección de anomalías (Z-score > 2)
    - Estacionalidad (día de semana, hora del día)
    - Comparación entre campañas del mismo cliente

    CAPA 3 — LLM via OpenRouter (Requiere 3-4 semanas)
    ─────────────────────────────────────────────────
    - Interpretación en lenguaje natural de los datos
    - Diagnóstico narrativo de por qué una campaña funciona o falla
    - Sugerencias contextualizadas al rubro del cliente
    - Generación del master brief HTML (como el del paso1)
    """

    async def run_analysis(self, org_id: str) -> list[AIInsight]:
        """Ejecuta las 3 capas en orden y genera insights."""
        insights = []
        insights += await self._run_rules(org_id)      # Capa 1
        insights += await self._run_statistics(org_id)  # Capa 2
        insights += await self._run_llm(org_id)         # Capa 3
        return insights
```

**Período de aprendizaje — Cronograma:**

| Semana | Capas activas | Output para el cliente |
|---|---|---|
| 1 | ✅ Reglas | Alertas críticas (spend cap, cuenta suspendida, campañas sin leads) |
| 2 | ✅ Reglas + ✅ Estadística | Tendencias de CPL, ranking de campañas, correlaciones |
| 3 | ✅ Reglas + ✅ Estadística + ⏳ LLM | Diagnóstico narrativo, campañas clasificadas ✅⚠️❌ |
| 4 | ✅ Todas activas | Master brief completo + primeras acciones propuestas |

---

## COMPONENTE 7: Integración OpenRouter

### `backend/app/services/ai/llm.py`

```python
"""
Cliente OpenRouter — gateway a múltiples modelos LLM.
Permite que cada cliente use el modelo que prefiera.
"""

# Modelos disponibles en OpenRouter
AVAILABLE_MODELS = {
    "gemini-2.5-flash": {
        "id": "google/gemini-2.5-flash-preview",
        "cost_per_1k_input": 0.00015,
        "cost_per_1k_output": 0.0006,
        "tier": "standard",
    },
    "gemini-2.5-pro": {
        "id": "google/gemini-2.5-pro-preview",
        "cost_per_1k_input": 0.00125,
        "cost_per_1k_output": 0.01,
        "tier": "premium",
    },
    "claude-sonnet-4": {
        "id": "anthropic/claude-sonnet-4",
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
        "tier": "premium",
    },
    "claude-opus-4": {
        "id": "anthropic/claude-opus-4",
        "cost_per_1k_input": 0.015,
        "cost_per_1k_output": 0.075,
        "tier": "enterprise",
    },
}

class OpenRouterClient:
    """
    - Una sola API key de OpenRouter, múltiples modelos
    - Cada organización tiene su modelo configurado
    - Fallback automático si un modelo falla
    - Tracking de tokens consumidos por cliente (para costos)
    """

    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    async def analyze(self, org: Organization, prompt: str, data: dict) -> str:
        """Envía datos de campaña al LLM y recibe análisis."""

    async def generate_brief(self, org: Organization, campaign_data: dict) -> dict:
        """Genera un master brief HTML equivalente al del paso1."""
```

---

## COMPONENTE 8: Infraestructura VPS HostGator

---

### 8.1 Setup del VPS

#### `scripts/setup-hostgator.sh`

```bash
#!/bin/bash
# ═══ Setup VPS HostGator para LejoVista ═══

echo "🚀 Configurando VPS HostGator para LejoVista..."

# 1. Actualizar sistema
apt update && apt upgrade -y

# 2. Firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (redirect a HTTPS)
ufw allow 443/tcp   # HTTPS
ufw --force enable

# 3. Fail2ban
apt install -y fail2ban
systemctl enable fail2ban

# 4. Docker + Docker Compose
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin

# 5. Crear usuario no-root
adduser --disabled-password --gecos "" lejovista
usermod -aG docker lejovista

# 6. Swap (HostGator VPS puede tener poca RAM)
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 7. Directorio del proyecto
mkdir -p /opt/lejovista
chown lejovista:lejovista /opt/lejovista

# 8. Directorio de backups
mkdir -p /backups/lejovista
chown lejovista:lejovista /backups/lejovista

# 9. Certbot para SSL
apt install -y certbot

echo "✅ VPS listo. Siguiente paso: clonar repo y docker compose up"
```

### 8.2 DNS para lrojas.dev (name.com)

```
# Registros DNS a crear en name.com:
#
# Tipo    Nombre              Valor                 TTL
# A       lrojas.dev          [IP_DEL_VPS]          300
# A       *.lrojas.dev        [IP_DEL_VPS]          300
# A       api.lrojas.dev      [IP_DEL_VPS]          300
#
# El wildcard *.lrojas.dev permite:
#   cliente1.lrojas.dev → dashboard del cliente 1
#   admin.lrojas.dev    → panel de administración
#   api.lrojas.dev      → backend FastAPI
#
# NOTA: Verificar que name.com soporte wildcard DNS (*.lrojas.dev).
# Algunos registrars no lo permiten. Si no lo soporta, crear A records
# individuales por cliente hasta migrar a Cloudflare.
```

### 8.3 SSL para lrojas.dev

```bash
# ══════════════════════════════════════
# AHORA (name.com) — Certificado manual
# ══════════════════════════════════════
# name.com NO tiene plugin de Certbot, hay que usar DNS challenge manual

certbot certonly --manual \
  --preferred-challenges dns \
  -d "lrojas.dev" \
  -d "*.lrojas.dev"

# Certbot te pedirá agregar un registro TXT en name.com:
#   _acme-challenge.lrojas.dev → [valor que te da certbot]
# Agregarlo manualmente, esperar 60s, y confirmar.
# Repetir cada 90 días (o automatizar con cron + API de name.com).

# ══════════════════════════════════════
# DESPUÉS (Cloudflare, en ~3 meses) — Automático
# ══════════════════════════════════════
# 1. Migrar DNS a Cloudflare
# 2. Instalar plugin: apt install python3-certbot-dns-cloudflare
# 3. Crear /root/.cloudflare.ini con API token
# 4. Ejecutar:
certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /root/.cloudflare.ini \
  -d "lrojas.dev" \
  -d "*.lrojas.dev"

# Auto-renovación (funciona solo con Cloudflare):
echo "0 3 1,15 * * certbot renew --quiet && docker exec lejovista-nginx-1 nginx -s reload" \
  | crontab -
```

---

### 8.4 Nginx para lrojas.dev

#### `nginx/conf.d/default.conf`

```nginx
# ═══ LejoVista — Nginx (lrojas.dev) ═══
# Fácil de migrar: solo cambiar "lrojas.dev" por el nuevo dominio

# Subdominios de clientes → Frontend
server {
    listen 443 ssl http2;
    server_name ~^(?<subdomain>.+)\.lrojas\.dev$;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://frontend:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Tenant-Slug $subdomain;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}

# API Backend
server {
    listen 443 ssl http2;
    server_name api.lrojas.dev;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;

    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Strict-Transport-Security "max-age=31536000" always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;

    location / {
        limit_req zone=api burst=10 nodelay;
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}

# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name *.lrojas.dev lrojas.dev;
    return 301 https://$host$request_uri;
}
```

---

## COMPONENTE 9: Seguridad de la IA Autónoma

Reglas que **nunca** se pueden violar, sin importar lo que el LLM sugiera:

```
┌──────────────────────────────────────────────────────────────┐
│          REGLAS INQUEBRANTABLES DE LA IA                      │
│          (hardcodeadas, no configurables)                     │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  CREACIÓN DE CAMPAÑAS                                        │
│  ✗ Siempre crear en estado PAUSED (nunca ACTIVE)             │
│  ✗ Máximo 3 campañas nuevas por día por cuenta               │
│  ✗ Geo SIEMPRE incluye Lima Región key:2771                  │
│  ✗ NUNCA incluir instagram_positions                         │
│  ✗ SIEMPRE incluir facebook_reels                            │
│  ✗ page_welcome_message OBLIGATORIO en creativos             │
│  ✗ Nombres sin tildes, ñ, emojis ni caracteres especiales    │
│                                                               │
│  MODIFICACIONES                                              │
│  ✗ Budget máximo: 150% del budget actual                     │
│  ✗ No puede eliminar campañas (solo pausar)                  │
│  ✗ No puede cambiar objective de campaña existente           │
│  ✗ Snapshot obligatorio antes de cada cambio (rollback)      │
│                                                               │
│  RATE LIMITING (API)                                         │
│  ✗ Máximo 3 POSTs por sesión                                 │
│  ✗ Pausa 30-50s entre POSTs                                  │
│  ✗ Máximo 15 GETs por sesión                                 │
│  ✗ Pausa larga cada 3 calls (45-75s)                         │
│  ✗ Si error en POST: pausar sesión completa                  │
│  ✗ Headers que imitan navegador en TODA llamada              │
│                                                               │
│  SEGURIDAD FINANCIERA                                        │
│  ✗ Verificar spend_cap antes de CUALQUIER creación           │
│  ✗ Si spend_cap agotado: DETENER todo                        │
│  ✗ No gastar más de [límite mensual del contrato]            │
│                                                               │
│  ROLLBACK AUTOMÁTICO                                         │
│  ✗ Si CPL sube >50% en 24h post-cambio → revertir           │
│  ✗ Si 0 conversiones en 48h post-creación → pausar           │
│  ✗ Si frequency > 4.0 → pausar campaña                      │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## COMPONENTE 10: Docker Compose

### `docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: lejovista
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 256mb
    volumes:
      - redis_data:/data
    ports:
      - "127.0.0.1:6379:6379"

  backend:
    build: ./backend
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"

  celery-worker:
    build: ./backend
    command: celery -A app.workers worker --loglevel=info --concurrency=2
    restart: unless-stopped
    depends_on: [backend]
    env_file: .env

  celery-beat:
    build: ./backend
    command: celery -A app.workers beat --loglevel=info --schedule=/tmp/celerybeat-schedule
    restart: unless-stopped
    depends_on: [celery-worker]
    env_file: .env

  frontend:
    build: ./frontend
    restart: unless-stopped
    depends_on: [backend]
    environment:
      NEXT_PUBLIC_API_URL: https://api.lrojas.dev
      NEXT_PUBLIC_BASE_DOMAIN: lrojas.dev
    ports:
      - "127.0.0.1:3000:3000"

  nginx:
    image: nginx:alpine
    restart: unless-stopped
    depends_on: [frontend, backend]
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro

volumes:
  postgres_data:
  redis_data:
```

### `.env.example`

```bash
# ═══ LejoVista — Variables de Entorno ═══

# --- Dominio (cambiar aquí para migrar) ---
BASE_DOMAIN=lrojas.dev
API_URL=https://api.lrojas.dev

# --- Base de Datos ---
DB_USER=lejovista
DB_PASSWORD=          # Generar: openssl rand -hex 32
DATABASE_URL=postgresql+asyncpg://lejovista:PASSWORD@postgres:5432/lejovista

# --- Redis ---
REDIS_PASSWORD=       # Generar: openssl rand -hex 16
REDIS_URL=redis://:PASSWORD@redis:6379/0

# --- Seguridad ---
SECRET_KEY=           # Generar: openssl rand -hex 64
ENCRYPTION_KEY=       # Generar: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# --- Meta Ads ---
META_APP_ID=
META_APP_SECRET=

# --- Google Ads ---
GOOGLE_ADS_DEVELOPER_TOKEN=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# --- OpenRouter (IA) ---
OPENROUTER_API_KEY=
DEFAULT_AI_MODEL=google/gemini-2.5-flash-preview

# --- Email (opcional) ---
RESEND_API_KEY=
```

---

## Costos Estimados (1 cliente)

| Concepto | Mensual |
|---|---|
| VPS HostGator (plan básico) | $20-30 |
| Dominio lrojas.dev | Ya lo tienes |
| OpenRouter (IA) | $5-20 |
| SSL (Certbot) | $0 |
| **Total** | **~$25-50/mes** |

---

## Roadmap de Desarrollo (12 semanas)

```mermaid
gantt
    title LejoVista — Roadmap (12 semanas)
    dateFormat YYYY-MM-DD
    axisFormat %b %d

    section Fase 1: Base (Sem 1-2)
    VPS HostGator + Docker + DNS        :a1, 2026-06-23, 3d
    PostgreSQL + modelos + migraciones  :a2, after a1, 3d
    Auth JWT + OAuth Meta               :a3, after a2, 4d
    Cifrado tokens + multi-tenancy      :a4, after a3, 3d

    section Fase 2: Sync (Sem 3-4)
    Meta Sync Service (paso1)           :b1, after a4, 5d
    Rate limiter + API logging          :b2, after a4, 3d
    Celery + cron jobs                  :b3, after b2, 3d

    section Fase 3: IA Análisis (Sem 4-6)
    Capa 1 reglas + Capa 2 estadística  :c1, after b1, 7d
    OpenRouter + Capa 3 LLM             :c2, after c1, 5d
    AI Planner (proponer acciones)      :c3, after c2, 4d

    section Fase 4: IA Ejecución (Sem 6-8)
    Modifier (modificar campañas)       :d1, after c3, 5d
    Executor (crear campañas - paso0)   :d2, after d1, 5d
    Rollback + verificación impacto     :d3, after d2, 3d

    section Fase 5: Frontend (Sem 3-9)
    Design system + dashboard           :e1, after a4, 5d
    Campañas + diagnósticos             :e2, after e1, 5d
    Cola de acciones IA                 :e3, after c3, 5d
    Reportes + master brief             :e4, after c2, 5d
    Panel admin                         :e5, after e2, 3d

    section Fase 6: Producción (Sem 10-12)
    Nginx + SSL lrojas.dev              :f1, after d3, 3d
    Backups + monitoring                :f2, after f1, 3d
    Testing + seguridad                 :f3, after f2, 5d
    Primer cliente en producción        :milestone, after f3, 0d
```

---

## Plan de Verificación

### Tests Automatizados
```bash
# Backend
cd backend && pytest tests/ -v --cov=app

# Frontend
cd frontend && npm test

# Seguridad
pip install bandit && bandit -r backend/app/
```

### Verificación Manual
- [ ] Conectar cuenta Meta real vía OAuth
- [ ] Sync completo con pausas anti-detección verificadas
- [ ] IA genera insights coherentes con datos reales
- [ ] IA propone acción → aprobar → ejecuta correctamente
- [ ] IA crea campaña completa (6 pasos) en estado PAUSED
- [ ] Rollback funciona si CPL sube post-cambio
- [ ] Subdominio `cliente1.lrojas.dev` muestra solo datos del cliente
- [ ] Backups se generan y son restaurables
- [ ] Migración de dominio funciona cambiando solo `.env` + DNS
