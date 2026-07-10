# Plan de Implementación — Correcciones y Mejoras de la Auditoría (jul 2026)

> Fuente: auditoría multi-rol del 2026-07-10. Cada ítem referencia su hallazgo (A-1, B-2, etc.).
> Orden: Fase 0 (quick wins) primero; las fases siguientes dependen de lo que la anterior deja listo.
> Convención: cada paso lista **archivos**, **qué hacer**, **cómo validar** y **esfuerzo** (S/M/L).

---

## FASE 0 — Quick Wins (Semana 1) · 10 pasos, todos S

Objetivo: que lo ya construido **se vea, no mienta y no se degrade solo**. Sin dependencias entre sí — se pueden hacer en cualquier orden (sugerido: 0.1 → 0.10). Cada paso = 1 commit.

### 0.1 Deduplicar insights (B-1) 🔴
- **Archivos:** `backend/app/services/ai/engine.py`, nuevo test en `backend/tests/`.
- **Qué:** en `run_analysis`, antes de insertar los insights de una cuenta, marcar `is_dismissed=True` en los insights previos NO descartados de esa misma cuenta (`UPDATE ai_insights SET is_dismissed=true WHERE ad_account_id=X AND is_dismissed=false`). Así cada análisis "reemplaza" su foto anterior y `/diagnostics` muestra solo el estado vigente.
- **Extra:** añadir `ai_insights` viejos (>90 días, descartados) al `snapshot_archiver` o a un delete en el job semanal D-3.
- **Validar:** test: correr `run_analysis` 2 veces → `list_active_insights` devuelve un solo set, no duplicados.

### 0.2 Mostrar el historial de aprendizaje en la UI (E-1) 🔴
- **Archivos:** `frontend/app/(dashboard)/campaigns/page.tsx` (nueva sección) o nueva ruta `frontend/app/(dashboard)/campaigns/learning/page.tsx`; `frontend/lib/api.ts` (ya tiene `getLearningHistory`).
- **Qué:** timeline de `GET /campaigns/learning-history`: por cada entrada, tarjeta con producto, fecha, badge de modo (🧪 exploración / 🎯 optimizado), riesgo, `analysis.summary`, cambios propuestos con su porqué, y placeholder "Resultado: en evaluación" (se llena en Fase 1). Enlace desde el panel "Qué está haciendo la IA" de `campaigns/new`.
- **Validar:** crear una campaña de prueba → aparece en el timeline.

### 0.3 WS + polling correcto en creación de campañas (D-3) 🔴
- **Archivos:** `frontend/app/(dashboard)/campaigns/new/page.tsx`.
- **Qué:** (1) suscribirse a `lastEvent.event_type === "campaign_creation"` del `WebSocketProvider` (el backend YA emite READY/FAILED en `creation_tasks.py:338`); al recibirlo, consultar el draft una vez y cerrar. (2) Subir `POLL_MAX_ATTEMPTS` de 40 a 120 (~5 min: la Saga tarda ~3 min por los `countdown=40`) como fallback. (3) Mensaje de progreso honesto: "creando en Meta, toma ~3 minutos".
- **Validar:** crear campaña → el éxito llega por WS antes de agotar el polling; matar el WS → el polling igual resuelve.

### 0.4 Eliminar el sistema de sync fantasma (A-2)
- **Archivos:** borrar `backend/app/workers/sync_tasks.py`, `backend/app/services/meta_sync_service.py`, `backend/app/api/v1/sync.py`; quitar su include en `backend/app/main.py` y en `celery_app.py` (`include` y task route `sync_tasks.*`).
- **Qué:** el flujo real es `campaigns/sync-trigger` → `tasks.sync_ad_account`. El stub crea SyncJobs que nunca terminan.
- **Validar:** grep sin referencias rotas; suite de tests en verde; `docker compose up` sin errores de import.

### 0.5 Debounce del análisis post-sync (D-1)
- **Archivos:** `backend/app/workers/tasks.py`.
- **Qué:** en `sync_ad_account`, al completar, en vez de `run_ai_analysis.delay(org_id)` directo: lock en Redis (`SET analysis_pending:{org_id} 1 NX EX 360`); solo si el SET tuvo éxito, encolar `run_ai_analysis.apply_async((org_id,), countdown=300)`. La tarea borra la clave al empezar. Resultado: N cuentas sincronizando → 1 análisis, 5 min después del primero.
- **Validar:** test con 3 syncs simulados de la misma org → un solo `run_ai_analysis` encolado.

### 0.6 Versión de Graph API configurable + upgrade (A-5)
- **Archivos:** `backend/app/config.py` (`META_API_VERSION: str = Field(default="v23.0")` — confirmar la versión vigente en el changelog de Meta antes), `backend/app/services/meta_ads/rate_limiter.py` (ambas URLs), `.env.example`.
- **Qué:** interpolar `settings.META_API_VERSION` en `call_api` y `call_api_multipart`. v19.0 está fuera de soporte.
- **Validar:** con `MOCK_SERVICES=false` y un token de prueba, un GET simple a `/me` responde 200 con la versión nueva.

### 0.7 Trazabilidad honesta del fallback del LLM (C-4)
- **Archivos:** `backend/app/services/ai/llm.py`, `backend/app/services/ai/engine.py`, `frontend/app/(dashboard)/reporte/page.tsx` y `diagnostics/page.tsx`.
- **Qué:** `analyze_campaigns` devuelve `(narrative, used_fallback: bool)`. Si `used_fallback`: `acc.narrative_provider = "fallback_template"`, `narrative_model = None`. El frontend, si `narrative_provider === "fallback_template"`, cambia el badge "Procesado por IA" por "Informe básico (IA no disponible)".
- **Validar:** test con API key inválida → provider queda `fallback_template`; con mock desactivado y key válida → provider real.

### 0.8 Unificar Leads/Conversaciones + rotular ventanas (E-2)
- **Archivos:** `backend/app/services/ai/engine.py` (`_get_recent_campaigns_summary`: exponer `conversations or conversions` como campo único `conversations`), `frontend/.../diagnostics/page.tsx` (rotular "Conversaciones", usar el campo unificado), ambos: badge visible "Últimos 7 días" / "Últimos 30 días" junto a cada bloque KPI.
- **Qué:** una sola métrica primaria con un solo nombre en todo el producto.
- **Validar:** misma cuenta → `/diagnostics` y `/reporte` muestran el mismo total de conversaciones para la misma ventana.

### 0.9 Healthchecks y Sentry en prod (D-2)
- **Archivos:** `docker-compose.prod.yml`, `backend/app/main.py` (endpoint `/health/deep`).
- **Qué:** (1) copiar los healthchecks del compose local (backend + 3 workers) al prod. (2) `/health/deep`: devuelve última `SyncJob` COMPLETED global y si hay alguna >24h vieja, `status: degraded`. (3) Documentar `SENTRY_DSN` como obligatorio de prod en `.env.example`.
- **Validar:** `docker compose -f docker-compose.prod.yml config` OK; matar un worker → `docker ps` lo muestra unhealthy.

### 0.10 `backup_state` real en el planner (A-3)
- **Archivos:** `backend/app/services/ai/planner.py`, test en `backend/tests/`.
- **Qué:** en `_create_action_from_insight`, incluir `parameters["backup_state"]`: para PAUSE → `{"status": "ACTIVE"}`; MODIFY_BUDGET → `{"daily_budget": old_budget}`; MODIFY_PLACEMENTS → targeting actual del snapshot del adset. Con esto el rollback del executor (`executor.py:71`) por fin es alcanzable.
- **Validar:** test: acción propuesta contiene `backup_state` coherente con el snapshot.

**Entrega Fase 0:** demo interna: dashboard sin alertas duplicadas, historial de IA visible, creación de campaña que termina bien en UI, prod monitoreable.

---

## FASE 1 — Cerrar el ciclo de aprendizaje (Semanas 2-3) · C-1 + B-3 + E-1b 🔴

**La promesa central del producto.** Depende de: 0.2 (UI donde mostrar el resultado).

### 1.1 Tarea de evaluación de resultados (outcome loop)
- **Archivos:** nuevo `backend/app/services/campaign_outcome.py`, `backend/app/workers/tasks.py` (nueva task), `backend/app/workers/celery_app.py` (beat + route a cola `ai`).
- **Qué:** task Beat diaria (ej. 8:00 Lima) `evaluate_campaign_outcomes`:
  1. Busca `CampaignLearningLog` con `status="CREATED"`, `outcome_json IS NULL` y `created_at + evaluation_window_days < hoy`.
  2. Resuelve el `campaign_id` real vía `draft_id → CampaignDraft.payload["result"]["campaign_id"]`. Si el draft falló o la campaña nunca se activó (sin snapshots), `outcome_json = {"verdict": "NOT_ACTIVATED"}`.
  3. Agrega snapshots de esa campaña en la ventana de evaluación: spend, conversaciones, costo/conv, CTR.
  4. Veredicto determinista (sin LLM): `WIN` si costo/conv ≤ suggested_budget/20 × 1.2 (el colchón que usó el suggester) o ≤ mediana de ganadoras; `LOSS` si 0 conversaciones con gasto o costo > 2× lo esperado; `NEUTRAL` en el medio. Guardar cifras + veredicto + hipótesis original en `outcome_json`, `status="EVALUATED"`.
- **Validar:** tests con snapshots sintéticos para WIN/LOSS/NEUTRAL/NOT_ACTIVATED.

### 1.2 Madurez que pesa resultados propios
- **Archivos:** `backend/app/services/campaign_learning.py`, `backend/app/services/campaign_suggester.py`.
- **Qué:** `assess_maturity` recibe además los learning_logs evaluados de la cuenta: (a) exige ≥2 `WIN` propios para declarar `converged` (además del CV actual); (b) si los últimos 2 veredictos son `LOSS`, fuerza modo `exploration` con razón explícita ("las últimas creaciones de la IA no rindieron; se vuelve a explorar"). El razonamiento de `build_analysis` menciona los outcomes ("de las N campañas que creé, X funcionaron").
- **Validar:** tests: cuenta convergida por CV pero con 2 LOSS recientes → exploration.

### 1.3 Resultado visible en la UI + explicador
- **Archivos:** frontend del 0.2; `backend/app/api/v1/campaigns.py` (learning-history ya devuelve `outcome`); opcional `llm.py` (prompt corto nuevo).
- **Qué:** en el timeline, reemplazar "en evaluación" por badge WIN/LOSS/NEUTRAL con las cifras reales (costo/conv logrado vs esperado). Opcional (anexo 6.2): mini-llamada LLM `explain_outcome` (2 frases, cifras deterministas inyectadas, `feature="outcome_explainer"` en token tracking).
- **Validar:** flujo completo en staging con `evaluation_window_days` reducido a 0 para probar.

**Entrega Fase 1:** demo al cliente: "la IA creó esta campaña, esta fue su hipótesis, este fue el resultado real, y esto cambió en su modo de decidir".

---

## FASE 2 — Un solo flujo de creación + write-back real (Semanas 3-5) · A-1, A-4, F-2 🔴

Depende de: 0.6 (versión API), 0.10 (backup_state). Es el prerrequisito de encender `ENABLE_META_WRITEBACK`.

### 2.1 Unificar CREATE_CAMPAIGN en la Saga (A-1)
- **Archivos:** `backend/app/services/ai/executor.py`, `backend/app/workers/creation_tasks.py`, test.
- **Qué:** eliminar `_execute_campaign_creation` (el flujo inline con el bug del `image_hash` mock). Cuando el executor recibe una acción `CREATE_CAMPAIGN` aprobada: crea un `CampaignDraft` desde `action.parameters` (que ya trae targeting, creative, presupuesto) y despacha **el mismo chain** de `creation_tasks`. La acción queda `EXECUTING` y se marca `COMPLETED/FAILED` desde el callback del chain (pasar `action_id` en el contexto; `task_create_ad` y `task_handle_chain_error` actualizan la acción si viene).
- **Nota:** la acción CREATE_CAMPAIGN del planner no trae `page_id`/`whatsapp_number` — resolverlos como hace `campaigns/new` (páginas de la org; si hay ambigüedad, dejar la acción en un estado `NEEDS_INPUT` visible en `/actions` en vez de adivinar).
- **Validar:** aprobar una acción CREATE_CAMPAIGN en modo simulado → draft creado, Saga completa, acción COMPLETED con los IDs; suite entera en verde.

### 2.2 MODIFY_PLACEMENTS válido para Meta (A-4)
- **Archivos:** `backend/app/services/ai/planner.py`, `backend/app/services/campaign_targeting.py`.
- **Qué:** construir el payload con `publisher_platforms: ["facebook"]` + `facebook_positions` = allowlist actual menos el placement señalado por el insight (reusar `_facebook_positions`). `old_placements` = targeting real del `AdsetSnapshot.targeting_summary`, no la lista hardcodeada.
- **Validar:** test: el planned_call no contiene `facebook_reels` como publisher_platform; positions coherentes con el snapshot.

### 2.3 Encendido controlado del write-back (F-2)
- **Qué (operativo, no solo código):**
  1. Validar FULL sync end-to-end contra Meta real (pendiente del HANDOFF) con la versión nueva de API.
  2. `ENABLE_META_WRITEBACK=true` solo para la cuenta piloto (si el flag es global, añadir allowlist `WRITEBACK_ACCOUNT_ALLOWLIST` en settings — S).
  3. Secuencia de prueba en piloto: (a) crear campaña vía UI → verificar en Ads Manager que existe PAUSADA con targeting correcto; (b) aprobar un MODIFY_BUDGET → verificar el cambio; (c) forzar un fallo (token inválido) → verificar rollback con `backup_state`.
  4. Auditar cada paso vía el historial de API calls del dashboard.
- **Validar:** checklist anterior firmado antes de habilitar a más cuentas.

**Entrega Fase 2:** el producto ejecuta de verdad en Meta, por un único camino de código, con reversa.

---

## FASE 3 — Clasificador único + Prompt v2 (Semanas 5-6) · C-2, C-3

Independiente de Fase 2 (puede paralelizarse si hay dos personas).

### 3.1 Clasificador único de campañas (C-2)
- **Archivos:** `backend/app/services/ai/thresholds.py` (añadir `classify_campaign(cost_per_conv, conversations, target_cpl) -> "GANADORA"|"BUENA"|"REGULAR"|"CARA"|"PAUSAR"`), `report_builder.py` (`_label` delega), `llm.py` (mock narrative y borrar los umbrales 2.0/7.0), `campaign_learning.py` (usar el mismo criterio en outcomes de Fase 1).
- **Qué:** una sola función con umbrales relativos al `target_cpl` dinámico (piso/techo ya existentes). Eliminar toda constante suelta de clasificación.
- **Validar:** test de tabla (cost, conv, target → label); misma campaña clasifica igual en `/reporte`, narrativa mock y outcomes.

### 3.2 Eliminar el HTML muerto (A-6)
- **Archivos:** `llm.py` (borrar `generate_html_brief`), `engine.py` (quitar la llamada y `html_brief` del retorno).
- **Nota:** si se prefiere, mover la plantilla a `backend/app/templates/pdf_brief.html` para la Fase 5.2 (PDF) en vez de borrarla.
- **Validar:** análisis corre sin regresiones; menos ~1 seg y ~300 líneas.

### 3.3 Master Brief v2 (C-3)
- **Archivos:** `llm.py` (`PROMPT_VERSION = "master-brief-v2"`), `engine.py` (armar el contexto rico).
- **Qué:** el user prompt inyecta: `target_cpl` de la cuenta + moneda/país explícitos; KPIs agregados 7d + comparativa vs 7d previos (Δ%); top-3 y bottom-3 campañas con su breakdown de placement/edad/dispositivo (del ReportBuilder, no recalcular); pacing (presupuesto diario total vs gasto real); métrica = conversaciones. Filtrar del `insights_summary` los INFO de "histórico insuficiente". Contrato de salida fijo: `## Resumen (≤80 palabras) / ## Ganadoras / ## En alerta / ## Fallidas / ## Prioridades de la semana (3 bullets)`, clasificando SOLO contra el target_cpl provisto.
- **Validar:** 3 corridas sobre los datos mock → misma estructura, clasificaciones consistentes con `classify_campaign`; revisar salida a mano.

### 3.4 Copy v2 (mejoras finas del anexo 6.2)
- **Archivos:** `llm.py` (`CAMPAIGN_COPY_PROMPT_VERSION = "campaign-copy-v2"`), `campaigns.py`, frontend `campaigns/new`.
- **Qué:** (a) inyectar `organization.industry` (campo ya existente) en el prompt; (b) pedir 2 variantes de headline/primary_text (array JSON) y dejar elegir en la UI; (c) instruir límites de Meta (headline ≤40 chars, description ≤30) y validarlos en `CampaignValidator`; (d) `response_format: {"type": "json_object"}` cuando el proveedor lo soporte (OpenAI/compatibles), manteniendo la regex como fallback.
- **Validar:** test del parser con 2 variantes; validator rechaza headline >40.

---

## FASE 4 — Rendimiento de datos (Semana 7) · B-2, E-4

Recomendado antes de sumar más cuentas reales.

### 4.1 Agregaciones en SQL
- **Archivos:** `rules.py` (CPL objetivo con `SELECT SUM(spend), SUM(conversions)` en vez de cargar todo), `planner.py` (budget_map/adset_map con corte de 30 días y `DISTINCT ON` o subquery de fecha máxima).
- **Validar:** mismos resultados que antes en tests; `EXPLAIN` usa `idx_snapshot_acc_date`.

### 4.2 Cache del ReportBuilder
- **Archivos:** `report_builder.py` o wrapper nuevo `report_cache.py`; invalidación en `tasks.py`.
- **Qué:** cachear `build(ad_account)` serializado en Redis (`report:{ad_account_id}:{window}`, TTL 10 min), invalidar al completar un sync de esa cuenta. Con esto `suggestion`, `risk-preview` (que se llama al tipear) y `create` comparten un build.
- **Validar:** dos risk-preview seguidos → un solo build (log/contador); tras sync, el cache se refresca.

### 4.3 KPIs agregados en el dashboard (E-4)
- **Archivos:** `backend/app/api/v1/campaigns.py` (`/recent` devuelve además `spend_7d`, `conversations_7d`, `cost_per_conv_7d` por campaña — o nuevo endpoint liviano sobre el cache 4.2), `frontend/app/(dashboard)/page.tsx`.
- **Qué:** el ranking "menor CPL" usa el agregado de 7 días, no el snapshot de un día.
- **Validar:** dashboard y reporte coinciden para la misma ventana.

---

## FASE 5 — Producto premium (Semanas 8-10) · E-3, F-3.1, F-3.2, E-6, E-5

### 5.1 Dashboard re-jerarquizado (E-3)
- **Archivos:** `frontend/app/(dashboard)/page.tsx`, `settings/page.tsx`.
- **Qué:** (1) fila KPI arriba: gasto 7d, conversaciones 7d, costo/conv con flecha de tendencia vs 7d previos, nº de alertas críticas (link a `/diagnostics`); (2) mover "Historial de llamadas API" a una pestaña/acordeón "Actividad técnica" o a Settings; (3) tarjetas resumidas de las 3 alertas más severas; (4) "Última sincronización: hace X" visible en el header (dato de `SyncJob` — exponer en `/recent` o endpoint liviano).
- **Validar:** revisión visual + screenshot.

### 5.2 Comparativa temporal en el reporte (F-3.1)
- **Archivos:** `report_builder.py` (`build` calcula también la ventana previa y añade `deltas` a los KPIs y por campaña), `reporte/page.tsx` (Δ% con color/flecha), selector 7/30/90 días (`window_days` como query param del endpoint).
- **Validar:** tests de deltas con datos sintéticos; selector cambia la ventana sin recargar.

### 5.3 PDF real (F-3.2)
- **Archivos:** nuevo `backend/app/services/pdf_report.py` + endpoint `GET /reports/pdf`; plantilla (reciclar la de 3.2 si se conservó); `reporte/page.tsx` (botón descarga real).
- **Qué:** render server-side con WeasyPrint (HTML→PDF, sin browser headless) desde los datos del ReportBuilder + narrativa. Marca de la organización (nombre/logo si existe).
- **Validar:** PDF abre en Preview/Acrobat, tablas no cortadas, generación <10 s.

### 5.4 Pulido UX (E-6)
- **Qué:** (a) modal de rechazo: focus trap + Escape + `aria-modal`; (b) badges con ícono además de color; (c) thumbnails de creativos en `/reporte` (la `url` ya viene — `<img>` con fallback); (d) marcar en el reporte las campañas creadas por la IA (cruzar con learning_logs por campaign_id) con badge "Creada por IA"; (e) `@media print` correcto mientras exista window.print.
- **Esfuerzo:** S cada uno; agrupar en un solo PR de pulido.

### 5.5 Tipado del frontend (E-5)
- **Archivos:** nuevo `frontend/lib/types.ts`, `frontend/lib/api.ts`, páginas.
- **Qué:** interfaces para CampaignSnapshot, AIInsight, AIAction, BriefData/Report, LearningLog, AiUsage (Suggestion ya existe en campaigns/new — moverla). Tipar los métodos de `api.ts` (`Promise<T>`); reemplazar `useState<any[]>` página por página.
- **Validar:** `npx tsc --noEmit` limpio; jest en verde.

---

## FASE 6 — SaaS comercializable (Trimestre) · C-5, F-3.3, F-3.5, D-2b, F-3.6

### 6.1 Reglas de campaña por tenant (C-5)
- **Archivos:** nuevo modelo `TenantCampaignRules` + migración Alembic (seed con los valores actuales de `campaign_defaults.py`); `organizations.py` (GET/PUT `/my/campaign-rules`); consumidores (`creation_tasks.py`, `campaign_targeting.py`, `planner.py`) leen de DB con fallback al default; `settings/page.tsx` (editor: geo, destino WhatsApp/web/form, placements requeridos/prohibidos, objetivo).
- **Qué:** desbloquea clientes fuera de Lima/WhatsApp. Extender también `get_thresholds_for_org` a overrides por org (tabla o JSON en organization).
- **Validar:** dos orgs con reglas distintas crean campañas con geo/destino distintos; tests por tenant.

### 6.2 Notificaciones salientes (F-3.3)
- **Archivos:** `notification_service.py` (canal email — SMTP/Resend), nueva preferencia por org, hooks en: insight CRITICAL nuevo (post-análisis), Saga completada ("campaña lista para activar"), sync FAILED permanente.
- **Validar:** email de prueba por cada evento en staging.

### 6.3 OAuth de Meta + long-lived tokens (F-3.5) — L
- **Archivos:** `auth.py`/nuevo `meta_oauth.py` (flujo OAuth: redirect, callback, exchange a long-lived, guardado cifrado en `MetaCredential`), `settings/page.tsx` (botón "Conectar con Facebook"), renovación programada (Beat mensual re-exchange).
- **Qué:** onboarding self-service; elimina el token manual de corta vida señalado en el HANDOFF.
- **Validar:** conectar una cuenta real de punta a punta sin tocar la DB a mano.

### 6.4 Observabilidad de colas (D-2b)
- **Qué:** Flower (o `celery events` + exporter Prometheus) en el compose de prod detrás de auth; alerta si una cola supera N pendientes o un worker no responde.
- **Esfuerzo:** S-M.

### 6.5 Billing por planes (F-3.6) — L, al final
- **Qué:** planes (nº cuentas, campañas/mes, tokens LLM/mes — el tracking de `ai_token_usage` ya da el medidor), integración de pago (Stripe o pasarela local), enforcement suave (avisos) antes que duro. Decisiones de pricing = producto, no técnicas: validar comercialmente antes de construir.

---

## Mapa de dependencias

```
FASE 0 (todos independientes)
  0.2 ──► 1.3 (UI del outcome)
  0.6 ──► 2.3 (API version antes del write-back)
  0.10 ─► 2.3 (rollback probado en piloto)
FASE 1 ──► (independiente de 2; puede correr en paralelo)
FASE 2: 2.1 ──► 2.3 ;  2.2 ──► 2.3
FASE 3: 3.1 ──► 3.3 (el prompt usa el clasificador) ; 3.2 ──► 5.3 (si se recicla plantilla)
FASE 4: 4.2 ──► 4.3 y 5.2 (cache alimenta KPIs y deltas)
FASE 5: 5.2 depende de 4.2 ; resto independiente
FASE 6: 6.1 idealmente tras 2.3 (reglas se prueban con write-back real)
```

## Reglas de ejecución

1. **Un commit por paso**, mensaje referenciando el hallazgo (ej. `fix(ai): dedupe de insights por cuenta (B-1)`).
2. **Test primero donde hay bug** (A-1, A-4, B-1, D-3): reproducir en test, luego arreglar.
3. Nada de write-back real hasta completar el checklist 2.3.
4. Los prompts nuevos siempre con **bump de versión** (`-v2`) — la trazabilidad ya existe, úsenla.
5. Al cerrar cada fase: actualizar HANDOFF.md con lo hecho y lo aprendido.

## Resumen de calendario

| Fase | Semanas | Tema | Hallazgos |
|---|---|---|---|
| 0 | 1 | Quick wins (10 pasos S) | B-1, E-1, D-3, A-2, D-1, A-5, C-4, E-2, D-2, A-3 |
| 1 | 2-3 | Ciclo de aprendizaje completo | C-1, B-3, E-1b |
| 2 | 3-5 | Creación unificada + write-back | A-1, A-4, F-2 |
| 3 | 5-6 | Clasificador único + prompts v2 | C-2, C-3, A-6, copy v2 |
| 4 | 7 | Rendimiento de datos | B-2, E-4 |
| 5 | 8-10 | Producto premium (dashboard/reporte/PDF/tipos) | E-3, F-3.1, F-3.2, E-5, E-6 |
| 6 | trimestre | SaaS (tenant rules, OAuth, notifs, billing) | C-5, F-3.3, F-3.5, D-2b, F-3.6 |
