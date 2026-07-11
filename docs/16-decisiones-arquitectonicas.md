# 16 · Decisiones Arquitectónicas (ADR implícitas)

Decisiones de diseño **inferidas del código** y su justificación. No existen ADRs formales en el repo; este documento las reconstruye.

## ADR-01 · Snapshots diarios idempotentes como modelo analítico
- **Contexto**: hay que analizar tendencias de campañas a lo largo del tiempo.
- **Decisión**: guardar una fila por entidad (campaña/adset/anuncio) y día, con *upsert* por clave natural `(cuenta, entidad, fecha)`.
- **Por qué**: idempotencia frente a syncs repetidos, consultas por rango de fechas triviales, y un modelo simple sin data warehouse.
- **Consecuencia**: crecimiento sin límite → se necesitó el **archivado** (`D-3`). Los IDs de Meta no son FKs (entidades que pueden no existir localmente).

## ADR-02 · Human-in-the-loop y triple gating de escrituras
- **Decisión**: las acciones se **proponen** y requieren aprobación; las escrituras reales están gateadas por `MOCK_SERVICES` + `ENABLE_META_WRITEBACK` + `WRITEBACK_ACCOUNT_ALLOWLIST`; las campañas se crean `PAUSED`.
- **Por qué**: la IA puede gastar dinero real; un error de parámetros es costoso e irreversible.
- **Consecuencia**: encendido gradual por cuenta piloto (`F-2`); overhead de aprobación por diseño.

## ADR-03 · La IA nunca inventa cifras (`AI-1`)
- **Decisión**: todo número sale de datos reales agregados en DB; el LLM solo redacta texto. El clasificador, el CPL objetivo, el riesgo, el sugeridor y el veredicto de outcome son **código determinista**.
- **Por qué**: un informe que decide gasto no puede basarse en alucinaciones; auditabilidad.
- **Consecuencia**: el LLM es opcional con fallback local; la lógica de negocio es testeable como funciones puras.

## ADR-04 · Cliente LLM unificado con auto-selección de proveedor
- **Decisión**: un `OpenRouterClient` contra endpoints OpenAI-compatibles; el proveedor se elige por qué API key esté presente (OpenAI > Gemini > OpenRouter).
- **Por qué**: portabilidad entre proveedores sin reescribir la integración; migrar es cambiar una env var.
- **Consecuencia**: normalización de nombres de modelo (`google/…`), y un selector que solo ofrece modelos del proveedor activo (`AI-4`).

## ADR-05 · Colas Celery dedicadas (`I-2`)
- **Decisión**: separar `sync`, `ai`, `creation` (+ `celery` de mantenimiento) en workers independientes.
- **Por qué**: un pico de syncs (que hacen 12-15 llamadas a Meta) no debe retrasar la ejecución de acciones aprobadas ni la creación de campañas.
- **Consecuencia**: se escalan por separado; más contenedores que operar.

## ADR-06 · Saga por pasos con `celery.chain` para crear campañas
- **Decisión**: la creación es un `chain` de 5 tareas (imagen → campaña → adset → creativo → anuncio) con `countdown=40s` entre pasos y `on_error`.
- **Por qué**: la creación tiene múltiples llamadas dependientes a Meta; la pausa reduce el riesgo de bloqueo de cuenta; cada paso persiste su avance para un polling real.
- **Consecuencia**: latencia de creación de varios minutos (aceptable, es asíncrono); el estado vive en `CampaignDraft.payload["result"]`.

## ADR-07 · Debounce de análisis con lock Redis (`D-1`)
- **Decisión**: tras completar un sync, agendar el análisis con `SET NX EX`; solo el primero de la org lo encola.
- **Por qué**: una org con N cuentas disparaba N análisis, cada uno iterando todas las cuentas y llamando al LLM → hasta N² llamadas.
- **Consecuencia**: un análisis diferido 5 min tras el primer sync; fail-open (mejor un análisis de más que ninguno).

## ADR-08 · Cache-aside del ReportBuilder (`B-2`)
- **Decisión**: cachear el reporte en Redis por `(cuenta, ventana)` con TTL 600 s; invalidar tras cada sync.
- **Por qué**: `ReportBuilder.build` se llama varias veces en el flujo de creación (`/suggestion`, `/risk-preview` al tipear, `/create`) y en el brief.
- **Consecuencia**: fail-open (si Redis cae, se construye sin cache).

## ADR-09 · Métrica primaria unificada: conversaciones con fallback a leads (`E-2`)
- **Decisión**: en todo el sistema, la conversión relevante es `conversations or conversions` (mensajes de WhatsApp; leads si no hay mensajería).
- **Por qué**: el producto vende campañas de mensajería a WhatsApp; unificar evita que `/diagnostics` y `/reporte` muestren números distintos.

## ADR-10 · Clasificador único relativo al `target_cpl` dinámico (`C-2`)
- **Decisión**: `classify_campaign` y `compute_target_cpl` centralizados en `thresholds.py`; el CPL objetivo se calibra con el histórico real de la cuenta (con piso/techo).
- **Por qué**: antes cada consumidor tenía su umbral absoluto en soles y la misma campaña salía "Buena" en un lado y "FALLIDA" en otro.

## ADR-11 · Multi-tenant por subdominio + aislamiento por cuenta
- **Decisión**: tenant = subdominio (`X-Tenant-Slug`) validado contra la pertenencia del usuario (`S-8`); cuenta activa vía `X-Ad-Account-Id` (`get_active_ad_account_id`).
- **Por qué**: separar clientes y, dentro de un cliente, no mezclar datos entre varias cuentas publicitarias.
- **Consecuencia**: 400 explícito si hay 2+ cuentas y no se elige una (en vez de "la primera" en silencio).

## ADR-12 · Una imagen de backend para API + workers + beat
- **Decisión**: misma imagen, distinto `command`.
- **Por qué**: simplifica build/publish/deploy y garantiza paridad de dependencias.
- **Consecuencia**: la imagen incluye todo (API y workers) aunque cada contenedor use un subconjunto.

## ADR-13 · Fallbacks fail-open en toda la capa Redis
- **Decisión**: throttle, blocklist, cache y pubsub degradan sin romper la request.
- **Por qué**: Redis es soporte, no fuente de verdad; la disponibilidad del producto no debe depender de él (excepto como broker de Celery).
- **Trade-off**: se acepta que un limitador/blocklist pueda no aplicar durante una caída de Redis (ver [15](15-deuda-tecnica.md) S-3).

---

## Mantenimiento automático sugerido

Estos documentos deberían regenerarse cuando cambie el código correspondiente:

| Documento | Regenerar cuando cambie |
|-----------|-------------------------|
| [06-api](06-api.md) | `app/api/v1/*.py`, `app/schemas/*.py` |
| [07-modelos](07-modelos.md) + ERD | `app/models/*.py` |
| [05-base-de-datos](05-base-de-datos.md) (cadena de migraciones) | `alembic/versions/*` |
| [10-configuracion](10-configuracion.md) | `app/config.py`, `.env.example` |
| [11-servicios-externos](11-servicios-externos.md) | `services/meta_ads/*`, `services/ai/llm.py` |
| [21-producto-datos-visualizados](21-producto-datos-visualizados.md) | `app/services/report_builder.py`, `frontend/app/(dashboard)/page.tsx`, `reporte/page.tsx`, `campaigns/page.tsx`, `frontend/lib/types.ts` |

Herramientas candidatas: exportar el OpenAPI (`/openapi.json`) para la referencia de API y los tipos del frontend; introspección de `Base.metadata` para el ERD.
