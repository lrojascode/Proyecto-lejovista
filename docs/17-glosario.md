# 17 · Glosario

## Términos de dominio

| Término | Significado |
|---------|-------------|
| **Tenant / Organización** | Cliente del SaaS (`Organization`), identificado por `slug` (subdominio) y `id` (UUID) |
| **AdAccount** | Cuenta publicitaria de Meta conectada (`external_account_id` = `act_XXX`) |
| **Snapshot** | Foto diaria de métricas de una entidad (campaña/adset/anuncio) |
| **Master Brief** | Informe narrativo + estructurado del rendimiento de una cuenta |
| **Insight** | Hallazgo del análisis (Capa 1/2): alerta o recomendación |
| **AIAction** | Acción de optimización propuesta por la IA (pausar, ajustar presupuesto, etc.) |
| **Saga** | Orquestación por pasos (Celery `chain`) para crear una campaña |
| **Draft** (`CampaignDraft`) | Estado de una creación de campaña en curso |
| **Outcome loop** | Ciclo que mide el resultado real de las campañas creadas por la IA |
| **Convergencia / Madurez** | Cuando la IA detecta una configuración ganadora estable y deja de experimentar |
| **CPL** | Costo por Lead (gasto / conversiones) |
| **Costo por conversación** | Gasto / conversaciones de WhatsApp (métrica primaria) |
| **CTR** | Click-Through Rate (clics / impresiones) |
| **Frecuencia** | Veces promedio que una persona vio el anuncio (>3 ≈ saturación) |
| **target_cpl** | CPL objetivo dinámico, calibrado con el histórico real de la cuenta |
| **Breakdown** | Desglose de métricas por dimensión (placement, edad, género, dispositivo, región) |
| **Placement** | Ubicación del anuncio (facebook_feed, facebook_reels, instagram_feed…) |
| **ABO / CBO** | Ad-set / Campaign Budget Optimization: dónde vive el presupuesto (adset vs campaña) |
| **Advantage+** | Audiencia amplia automatizada de Meta |
| **Write-back** | Escritura real hacia Meta (crear/modificar/pausar) |
| **Rubro / Industry** | Sector del negocio; determina qué políticas de Meta se recuerdan |

## Estados

| Entidad | Estados |
|---------|---------|
| `AIAction.status` | `PROPOSED` → `APPROVED`/`REJECTED` → `EXECUTING` → `COMPLETED`/`FAILED` (o `NEEDS_INPUT`) |
| `CampaignDraft.status` | `DRAFT` → `PENDING_CREATION` → `CREATED`/`FAILED` |
| `CampaignLearningLog.status` | `PROPOSED` → `CREATED` → `EVALUATED` |
| `SyncJob.status` | `PENDING` → `RUNNING` → `COMPLETED`/`FAILED` |
| `AdAccount.status` | `ACTIVE`, `PAUSED`, `EXPIRED`, `SUSPENDED`, `ARCHIVED` |
| `MetaCredential.status` | `ACTIVE`, `EXPIRED`, `REVOKED` |
| Etiqueta de campaña | `GANADORA`, `BUENA`, `REGULAR`, `CARA`, `PAUSAR` |
| Veredicto de outcome | `WIN`, `LOSS`, `NEUTRAL`, `NOT_ACTIVATED` |

## Códigos de auditoría (usados en comentarios del código)

Prefijos de hallazgos documentados en `HANDOFF.md` / `plan-implementacion-auditoria.md`. Aparecen como comentarios que explican *por qué* se hizo un cambio:

| Prefijo | Área |
|---------|------|
| `A-*` | Acciones / ejecución (ej. `A-1` unificación de creación, `A-3` persistencia de backup, `A-4` placements) |
| `B-*` | Bugs de datos/consultas (ej. `B-1` dedupe de insights, `B-2` agregación en SQL) |
| `C-*` | Consistencia de reporting/IA (ej. `C-2` clasificador único, `C-3` brief v2, `C-4` marca de fallback) |
| `D-*` | Operación/datos (ej. `D-1` debounce, `D-2` Sentry/healthcheck, `D-3` archivado, `D-4` trazabilidad narrativa) |
| `S-*` | Seguridad (ej. `S-6` token expirado, `S-8` org activa, `S-9` refresh tokens) |
| `I-*` | Infraestructura (ej. `I-2` colas dedicadas) |
| `AI-*` | Reglas de la IA (ej. `AI-1` no inventar cifras, `AI-2` nivel adset, `AI-3` histórico insuficiente, `AI-4` trazabilidad) |
| `E-*` | Experiencia/UX (ej. `E-2` métrica unificada, `E-3` dashboard, `E-4` agregado 7d, `E-5` tipos, `E-6` accesibilidad) |
| `F-*` | Fases del plan (ej. `F-2` encendido controlado, `F-3` reporte/PDF) |
| `P-*` | P-1 simulación marcada, `P-2` breakdowns/reporting extendido |
| `Bug N` / `Ajuste N` | Referencias a bugs concretos y ajustes del plan de creación de campañas |

> Estos códigos son **trazabilidad histórica**, no una taxonomía formal. El significado exacto de cada uno vive en `HANDOFF.md`.

## Siglas técnicas

| Sigla | Significado |
|-------|-------------|
| **JWT** | JSON Web Token |
| **JTI** | JWT ID (identificador único de token, para revocación) |
| **RBAC** | Role-Based Access Control |
| **ORM** | Object-Relational Mapping (SQLAlchemy) |
| **RSC** | React Server Components |
| **SPA** | Single Page Application |
| **CBO/ABO** | Campaign/Ad-set Budget Optimization |
| **UGC** | User-Generated Content (video) |
| **CoT** | Chain of Thought (tokens de razonamiento del LLM) |
| **TTL** | Time To Live |
| **CV** | Coeficiente de variación (estabilidad del CPL) |
