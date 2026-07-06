# Plan — Creación de campañas en la app (LejoVista)

**Fecha:** 2026-07-06 · **Basado en:** reunión Alejandro 2026-07-05 ([NotasReunionAlejandroMarketink.md](NotasReunionAlejandroMarketink.md)) + [PLAYBOOK-META-ADS-IA.md](PLAYBOOK-META-ADS-IA.md) + código real.

## Decisiones tomadas (2026-07-06)
1. **Rol IA/humano:** la IA **sugiere** parámetros según rendimiento previo; el cliente revisa/edita y aprueba; la app crea **todo en PAUSED**; el humano **activa** manualmente en Ads Manager. (Nunca activar por API — Regla de Oro #3 del playbook.)
2. **Write-back:** construir todo bajo simulación (`ENABLE_META_WRITEBACK=false` / `SIMULATE_META_WRITES=true`); validar contra cuenta real/sandbox solo al final.
3. **Orden:** fundación primero (F0→F2), luego motor de sugerencia (F3), descubrimiento de página (F4), UI (F5), video+tests (F6).

---

## Estado actual (reconciliado contra el código)

- **Frontend** [campaigns/new/page.tsx](frontend/app/(dashboard)/campaigns/new/page.tsx): formulario crudo; **no manda token** (401 asegurado); polling falso (`setInterval` de 3 ciclos → siempre "éxito").
- **Endpoint** [POST /campaigns/](backend/app/api/v1/campaigns.py:158): valida cuenta contra Meta (real) y despacha `chain` con `countdown=40`. OK como esqueleto.
- **Saga** [creation_tasks.py](backend/app/workers/creation_tasks.py): **100% mock** — IDs falsos, no llama a Meta, no persiste `CampaignDraft`, notifica a `"mock-tenant-id"`. Crea adset/ad en **ACTIVE** (viola PAUSED).
- **Dos stacks Meta:** creación usa `MetaApiClient` (v19) para validar; el executor de IA usa `MetaAPIRateLimiter`+`MetaCampaignModifier` (POST real, gating `SIMULATE_META_WRITES`, backoff, auditoría, paginación). **Consolidar sobre el segundo.**
- `CampaignDraft` y `MetaPage`: modelos definidos, **sin usar**.
- `MetaAssetRepository.get_or_upload_image`: mock.

## Discrepancias vs. playbook/reunión que el plan cierra
- 🔴 Todo debe crearse **PAUSED** (hoy adset/ad en ACTIVE) — `creation_tasks.py:83` y `:137`.
- 🟡 **IA sugiere parámetros** según data previa (visión central de la reunión) — hoy usa constantes fijas MTV.
- 🟡 **WhatsApp** roto en la reunión: fijar `whatsapp_phone_number` en `promoted_object` desde la creación.
- 🟡 Anti-bloqueo: mostrar **nivel de riesgo** antes de crear + tope de campañas/sesión (playbook §2).
- 🟡 Nomenclatura `MARCA_TIPO_PUBLICO_PRODUCTO_MESAA` por tenant (hoy hardcode MTV).
- 🟢 Soporte de video/UGC y subida real de imagen.

---

## Fase 0 — Consolidar sobre el stack de escritura correcto (fundacional) ✅ 2026-07-06
**Objetivo:** que la Saga cree de verdad (bajo simulación) usando el stack probado del executor.
- [x] Reescribir las 5 tareas de [creation_tasks.py](backend/app/workers/creation_tasks.py) para usar `MetaAPIRateLimiter` (no `MetaApiClient`). Cada POST respeta `SIMULATE_META_WRITES`, backoff y auditoría.
- [x] Implementar POST real multipart a `/{act}/adimages` en `MetaAssetRepository` (con rama mock cuando simula) — nuevo `MetaAPIRateLimiter.call_api_multipart`.
- [x] Pasar `tenant_id` real por el `context` de la Saga (hoy `"mock-tenant-id"`) — ahora viaja en `initial_context["tenant_id"]` y se bindea también al callback de error (`task_handle_chain_error.s(draft_id, tenant_id)`).
- [x] Errores de Meta se clasifican con `classify_meta_error` (transitorio → Celery reintenta vía `autoretry_for`; permanente → corta el chain).
- [x] **Bug encontrado y corregido en el camino:** `external_account_id` ya incluye el prefijo `act_` (confirmado contra el uso real en `sync.py`); el código nuevo casi lo duplicaba (`act_act_...`). Corregido en las 4 tareas + `MetaAssetRepository`.
- [x] 10 tests nuevos en `tests/test_creation_tasks.py` (reuso de hash, subida real, endpoint correcto, PAUSED en el payload de campaña, clasificación transitoria/permanente de errores, WhatsApp en `promoted_object`, payload del creativo, notificación con tenant real, callback de error con tenant real). Suite completa: **73 passed**.
- **DoD:** con `ENABLE_META_WRITEBACK=false`, la Saga corre completa, loguea cada POST simulado y no rompe. ✅

## Fase 1 — Persistencia y estado honesto ✅ 2026-07-06
- [x] Guardar `CampaignDraft` real al iniciar (`status=PENDING_CREATION`, `payload` = request); escribir `campaign_id/adset_id/creative_id/ad_id` conforme avanza (en `payload["result"]` vía el helper `_merge_draft_result`); `CREATED`/`FAILED` al final.
- [x] Arreglar `task_handle_chain_error` para marcar el draft `FAILED` con `error_message` real y notificar al tenant real (ya no `"mock-tenant-id"`, resuelto en Fase 0).
- [x] `GET /campaigns/drafts/{id}` (scopeado por tenant, 404 cross-tenant) para estado real — reemplaza el polling falso (`setInterval` de 3 ciclos) que tenía el frontend.
- [x] 4 tests nuevos de persistencia en `test_creation_tasks.py` (campaign_id/name persistido, status `CREATED` al final, `FAILED` con mensaje real, no-op seguro si el draft no existe) + 4 tests del endpoint en `test_campaign_drafts.py` (auth, 200 con progreso real, 404 cross-tenant, 404 si no existe). Suite completa: **81 passed**.
- **DoD:** un draft refleja el estado real de la Saga de punta a punta. ✅

## Fase 2 — Seguridad y reglas del playbook ✅ 2026-07-06
- [x] **Todo PAUSED**: `task_create_adset`/`task_create_ad` ahora usan `TENANT_CAMPAIGN_RULES["default_status"]` (PAUSED) en vez del `"ACTIVE"` hardcodeado. Tests: `test_task_create_adset_is_always_paused`, `test_task_create_ad_is_always_paused`.
- [x] `whatsapp_phone_number` en `promoted_object`: **ya estaba correcto desde la Fase 0** (se propaga `context["whatsapp_number"]` al crear el adset) — verificado con el test `test_task_create_adset_includes_whatsapp_number_in_promoted_object` de la Fase 0. No hizo falta código nuevo, solo confirmar que el bug de la reunión no aplica al código actual.
- [x] Nomenclatura `MARCA_TIPO_PRODUCTO_ZONA_MESAA`: nuevo helper `_brand_code(ad_account)` en [creation_tasks.py](backend/app/workers/creation_tasks.py) deriva la marca del `AdAccount.name` real (ej. "Althea Pack" → `ALTHEAPAC`) en vez del literal `"MTV"` hardcodeado para todos los tenants. Se documentó en [campaign_defaults.py](backend/app/tenant_rules/campaign_defaults.py) que el resto de reglas (geo/placements/objetivo) siguen centralizadas por diseño (v1.0) — mover eso a una tabla por tenant es un refactor más grande, fuera de alcance de esta fase. Test: `test_task_create_campaign_derives_brand_from_ad_account_name_not_hardcoded`.
- [x] Endpoint devuelve **nivel de riesgo** (`risk_level: "MEDIUM"`, playbook §2: toda creación en PAUSED es riesgo medio) y aplica **tope de 5 campañas nuevas por organización en una ventana móvil de 24h** (`MAX_CAMPAIGNS_PER_SESSION`/`CAMPAIGN_SESSION_WINDOW_HOURS` en [campaigns.py](backend/app/api/v1/campaigns.py)) → 429 al superarlo. Tests: `test_create_campaign_returns_medium_risk_level`, `test_create_campaign_blocks_after_session_cap`, `test_create_campaign_session_cap_scoped_per_organization`.
- [x] 6 tests nuevos. Suite completa: **87 passed**.
- **DoD:** ninguna creación produce objetos ACTIVE; WhatsApp correcto; naming por marca real; tope anti-bloqueo activo. ✅

## Fase 3 — Motor de sugerencia de parámetros (visión de la reunión)

Se separa en dos capas con responsabilidades distintas (evita mezclar cifras con creatividad):

### 3A — Sugerencia determinista (SIN LLM, como `ReportBuilder`)
- [ ] Servicio `CampaignSuggester`: lee snapshots + breakdowns (edad/género/placement/dispositivo/región) + `ReportBuilder`; detecta la ganadora (menor costo/conversación); deriva **con aritmética directa** objetivo, público, placements a excluir (los que dan S/0 conv), rango de edad, geo y presupuesto baseline. Cada parámetro viene con su **evidencia numérica** (regla AI-1: sin cifras inventadas).
- **DoD:** los parámetros estructurales salen de la data real, no de constantes MTV ni del LLM.

### 3B — Capa de prompting / comunicación con la API de ChatGPT (LLM)
El LLM se usa **solo para lo creativo** (copy), no para inventar números. Se apoya en el `OpenRouterClient` existente ([llm.py](backend/app/services/ai/llm.py)), que ya enruta a OpenAI/ChatGPT (`gpt-5.4-mini`) → Gemini → OpenRouter → mock.
- [ ] Nuevo método `suggest_campaign_copy(org, ad_account, winning_context, product, zone)` en `OpenRouterClient`:
  - **Entrada:** contexto de la campaña ganadora (copy/CTA/estructura de la mejor campaña, extraído en Fase 3A) + producto/zona objetivo.
  - **Salida:** **JSON estructurado** (`response_format`/parseo estricto): `{ "headline", "primary_text", "description", "welcome_message" }`. Nada de HTML ni prosa libre.
  - **System prompt:** reutiliza la regla anti-alucinación (no inventar métricas) + estructura de copy ganadora del playbook §6 (**DOLOR → PROMESA → BENEFICIOS ✅ → URGENCIA → CTA 📲**), tono WhatsApp Perú, emojis y saltos de línea.
  - **Guardrails de políticas de Meta** (error conocido #7 del playbook): instruir al modelo a evitar lenguaje farmacéutico ("fórmula magistral / medicamento / dosis / tratamiento") y reencuadrar como cuidado personal/cosmético.
  - `temperature` ~0.7 (más creatividad que el brief, porque aquí sí redacta), pero **los números del formulario NO pasan por el LLM**.
- [ ] **Versionado del prompt:** `CAMPAIGN_COPY_PROMPT_VERSION` (paralelo a `PROMPT_VERSION`), persistido en el `CampaignDraft` para trazar qué prompt generó cada copy (AI-4).
- [ ] **Fallback determinista:** si `MOCK_SERVICES` o no hay API key o el LLM falla/deuelve JSON inválido → plantilla de copy local basada en el copy ganador (mismo patrón `_generate_mock_narrative`). Nunca romper la creación por caída del LLM.
- [ ] **Reemplazar el stub hardcodeado** `CREATE_CAMPAIGN` del planner ([planner.py:192](backend/app/services/ai/planner.py:192)) para que use este servicio en vez de MTV/THHN/`S/.2.50` inventado.
- **DoD:** `suggest_campaign_copy` devuelve JSON válido contra ChatGPT real (probado 1 vez) y degrada a plantilla local sin API key.

### 3C — Endpoint que une ambas capas
- [ ] `GET /campaigns/suggestion?ad_account_id=...&product=...&zone=...` → formulario pre-rellenado (parámetros de 3A + copy de 3B) + el "por qué" (hallazgos que respaldan cada parámetro).
- **DoD:** abrir "Crear campaña" trae parámetros y copy sugeridos; el cliente solo revisa/edita y aprueba.

## Fase 4 — Descubrimiento de página + WhatsApp
- [ ] Poblar `MetaPage` en el sync vía `/me/accounts` (`page_id`, WhatsApp, `instagram_actor_id`).
- [ ] Dropdown de página en la UI en vez de pedir `page_id`/`whatsapp` a mano → elimina la fuente del bug de WhatsApp.

## Fase 5 — UI de creación "revisar y activar"
- [ ] Reescribir [campaigns/new/page.tsx](frontend/app/(dashboard)/campaigns/new/page.tsx) con el cliente autenticado ([lib/api.ts](frontend/lib/api.ts)).
- [ ] Mostrar parámetros sugeridos (F3), permitir editar copy/presupuesto, mostrar **nivel de riesgo**, y hacer **polling real** del draft.
- [ ] Post-creación: aviso "creada en PAUSED — actívala tú en Ads Manager" + link directo (playbook §5).

## Fase 6 — Video, tests y validación real
- [ ] `video_data` (thumbnail + `video_id`) en `CreativeBuilder`.
- [ ] Tests backend (fake limiter con 2 páginas, patrón de la casa): todo-PAUSED, persistencia del draft, error/rollback de la Saga, `CampaignSuggester`.
- [ ] Validación end-to-end con `ENABLE_META_WRITEBACK=true` contra cuenta sandbox: 4 IDs creados, todos PAUSED, WhatsApp correcto.

---

## Riesgos / notas
- No tocar `ENABLE_META_WRITEBACK` hasta F6; todo el desarrollo es simulado.
- Respetar espaciado (`countdown=40`) y tope de sesión — el bloqueo de cuenta es el riesgo #1 de la reunión.
- Reutilizar el stack del executor evita reabrir la duplicación A-3 del HANDOFF.
