# Handoff — Remediación auditoría 2026

Estado al **2026-07-04 (noche)**, reconciliado contra el código real (no contra lo que
dicen los resúmenes de sesiones anteriores). Referencia de hallazgos: [auditoria2026.md](auditoria2026.md).
Hay también un segundo informe hecho por otra herramienta: [auditoria2026-opus4.6.md](auditoria2026-opus4.6.md)
(mismo comité, otro modelo) — sus hallazgos ya están incorporados abajo donde aportaban algo nuevo.

**Este documento es la fuente de verdad para retomar el trabajo.** No asumas que los
resúmenes de "lo que hice en la sesión anterior" pegados en el chat son exactos —
en esta revisión encontré al menos un caso donde el resumen no coincidía con lo
realmente commiteado (ver sección "Discrepancias encontradas").

---

## 0. Qué hay que hacer ANTES de tocar código nuevo (5-10 min)

1. **Hay cambios sin commitear en `main` ahora mismo** (routing multi-proveedor de LLM).
   Decidir si se commitean tal cual, se completan (falta test) o se descartan:
   ```bash
   git diff backend/app/config.py backend/app/services/ai/llm.py
   ```
2. **Archivos sueltos sin decisión tomada** (ver sección 5) — no hacer nada con ellos
   todavía, solo confirmar con el usuario si son basura o WIP a conservar:
   `auditoria2026-opus4.6.md`, `backend/test_db.py`, `informe-rendimiento-jun2026.html`,
   y los borrados no commiteados de `auditoria26.md` / `plan.md` / cambios en `status.md`.
3. Correr la suite para confirmar que el punto de partida está verde:
   ```bash
   cd backend && ./run_tests.sh -q      # esperado: 33 passed
   cd ../frontend && npx jest            # esperado: 3 suites, 7 tests
   ```

---

## 1. Qué se hizo (todo commiteado salvo lo señalado en §0)

### 1.1 — Remediación de la auditoría (esta asistente, fases 1-11, commits `fca0ae9`…`94ca11d`)
Seguridad (claves rotadas y sacadas de `.env.example`, auth en `/sync` y WebSocket,
sin anti-detección de Meta, expiración de tokens, refresh JWT + revocación, tenant
activo real), resiliencia de Celery (reintentos/backoff, Beat con cron+jitter),
motor de IA sin cifras inventadas, endurecimiento de DB (unicidad, índices, FKs,
metadatos de narrativa), observabilidad (Sentry, logging, cooldowns), y **P-2 completo**
(modelo de adset/ad/creativo/audiencia, sync real y mock, agregador `ReportBuilder`,
API y página `/reporte`). 33 tests backend + 7 frontend.

### 1.2 — Sesión con otra herramienta #1: cambio de proveedor LLM (⚠️ sin commitear)
- Diagnóstico de que los breakdowns "no se veían" era caché de Docker (`docker compose up -d --build`
  sin rebuild real) — no era un bug de código. Correcto, sin cambios de código para eso.
- Cambio real de código (**sin commit**): `backend/app/config.py` y
  `backend/app/services/ai/llm.py` ahora enrutan `OpenRouterClient` a OpenAI si hay
  `OPENAI_API_KEY`, si no a Gemini directo si hay `GEMINI_API_KEY`, si no a OpenRouter.
  `.env` real tiene `OPENAI_API_KEY` + `DEFAULT_AI_MODEL=gpt-5.4-mini`.
- Se hizo un `UPDATE` manual en Postgres sobre `organizations.ai_model` para forzar
  `"gpt-5.4-mini"` en todas las orgs (bypasseando el flujo normal de Settings por org).

### 1.3 — Sesión con otra herramienta #2: rediseño visual + Docker + reporting real (commits `d399e22`…`efd57c2`, 12 commits)
- **Rediseño visual completo** (cierra **F-4** de la auditoría): tokens "midnight"
  (`--color-void`, `--color-carbon`, acento `acid-lime`), tipografía Inter + JetBrains
  Mono vía `next/font`, iconos SVG propios, las 8 páginas del dashboard + login +
  landing migradas. **Verificado:** no quedan clases Tailwind claras muertas en el
  dashboard; `globals.css` tiene los tokens nuevos.
- **Fix de infraestructura Docker**: frontend pasó de `next dev` (moría por memoria,
  512MB/1CPU, `ERR_EMPTY_RESPONSE`) a build de producción (`next build` + `next start`).
  **Verificado:** `frontend/Dockerfile` y `docker-compose.yml` reflejan el cambio.
- **P-2.7 (extensión de P-2, no estaba en el plan original)**: el sync real ahora
  también trae **breakdowns reales de Meta** (`_sync_real_breakdowns`: placement,
  edad, género, dispositivo, región — antes solo el mock los llenaba); se distingue
  "sin audiencias" (lista vacía, normal) de "sin permiso" (error, posible falta de
  scope del token) en logs y en el reporte; `ReportBuilder` genera **hallazgos y
  recomendaciones en texto** derivados 100% de los números agregados (no del LLM,
  consistente con AI-1) y el frontend los renderiza con barras de distribución.
- Un test de frontend preexistente y roto (`page.test.tsx`, importaba una página
  vieja) se corrigió. **Verificado:** los 3 suites de jest pasan.

---

## 2. Reconciliación hallazgo por hallazgo (contra el código, no contra resúmenes)

### 🔒 Seguridad
| # | Hallazgo | Estado |
|---|---|---|
| S-1 | Claves committeadas | ✅ Rotadas, `.env.example` con placeholders. **Corrección a un audit report externo**: `.env` **nunca estuvo trackeado por git** (`git log --all -- .env` vacío) — la `GEMINI_API_KEY`/`OPENAI_API_KEY` viven solo en el `.env` local gitignored, no hay exposición en el repo/historial. Sigue siendo buena práctica rotarlas si circularon por fuera del repo (chat, capturas), pero no es del mismo calibre que el hallazgo original. |
| S-2 | Auth en `/sync` | ✅ |
| S-3 | Auth en WebSocket | ✅ |
| S-4 | Anti-detección / Meta ToS | ✅ |
| S-5 | Tokens en prompts manuales (`paso0`/`paso1`) | ❌ **Sin tocar** — es un workflow externo a la app, no un cambio de código. Sigue siendo un riesgo de negocio/compliance, no técnico. |
| S-6 | Expiración de tokens | ✅ + reforzado: el sync real ahora marca la cuenta `EXPIRED` ante `OAuthException code 190` (esto se agregó **después** del handoff anterior, ya en esta línea de trabajo). |
| S-7 | `print(DEBUG)` | ✅ |
| S-8 | Tenant activo real | ✅ |
| S-9 | Refresh JWT + revocación | ✅ |
| S-10 | Cliente Meta hardcodeado | ✅ |

### 🏛️ Arquitectura
| # | Hallazgo | Estado |
|---|---|---|
| A-1 | Tareas Celery no registradas | ✅ |
| A-2 | Reintentos/backoff | ✅ |
| A-3 | Consolidar 2 stacks de Meta | ✅ **Cerrado (2026-07-05).** `master_brief_generator.py` y `gemini_client.py` borrados (código muerto confirmado — `ReportBuilder` los reemplazó). `meta_api_client.py` + `rate_limiter.py` (el otro stack) se quedan, los usa `campaigns.py` (creación de campañas, S-10). |
| A-4 | GET de reportes sin efectos | ✅ |
| A-5 | Loop de Celery robusto | ✅ |
| A-6 | Testing | ✅ el núcleo, pero ⚠️ **la funcionalidad agregada en la sesión #2 (breakdowns reales, hallazgos/recomendaciones) no tiene ningún test.** El resumen de esa sesión dice "20 tests backend en verde" — la suite real tiene **33**, sin tests nuevos para esa lógica. No es mentira necesariamente (puede haber corrido un subconjunto), pero **no hay cobertura real de esas ~150 líneas nuevas de negocio**. |

### 🗄️ Base de datos
| # | Hallazgo | Estado |
|---|---|---|
| D-1/D-2 | Unicidad + índice en snapshots | ✅ |
| D-3 | Particionado/archivado | ✅ **Cerrado (2026-07-05)** con archivado periódico (no particionado nativo): `snapshot_archiver.py` + tablas `_archive` + Celery task semanal. Ver sección 4 punto 8. |
| D-4 | Metadatos de narrativa | ✅ (pero ver AI-4 abajo: no registra el *proveedor* LLM) |
| D-5 | `create_all` gateado / Alembic | ✅ en código, pero en la práctica `AUTO_CREATE_TABLES=true` sigue siendo el mecanismo real en local — cuidado, ya nos mordió dos veces (columnas/tablas nuevas no aparecían hasta hacer `--build`, no `restart`). |
| D-6 | FK `ondelete` | ✅ |

### 🤖 Motor de IA
| # | Hallazgo | Estado |
|---|---|---|
| AI-1 | Cifras inventadas | ✅ mock corregido + prompt real con regla anti-alucinación. La sesión #1 (routing LLM) no rompió esto — la regla de sistema sigue en `analyze_campaigns`. |
| AI-2 | Presupuesto real del planner / **resolver adset real antes del write-back** | ✅ **Cerrado (2026-07-05).** El planner resuelve `target_adset_id` real desde `adset_snapshots` (solo si no hay ambigüedad); el modifier/rollback apuntan al adset para presupuesto/targeting/placements y a la campaña para pausar/reanudar. `ENABLE_META_WRITEBACK` sigue en `false` — activarlo es una decisión de negocio aparte. |
| AI-3 | Histórico insuficiente | ✅ |
| AI-4 | Versionado de prompts | ✅ **Cerrado (2026-07-05).** `PROMPT_VERSION` existe, y ahora `AdAccount.narrative_provider` (openai/gemini_direct/openrouter) se persiste junto a `narrative_model`, expuesto en `GET /reports/brief-data`. |
| AI-5 | Umbrales centralizados | ✅ |
| AI-6 | Flag de write-back | ✅ (`ENABLE_META_WRITEBACK=false`, sigue así — correcto, ver AI-2) |

### ⚙️ Infra
| # | Hallazgo | Estado |
|---|---|---|
| I-1 | Beat cron + jitter | ✅ |
| I-2 | Colas dedicadas (sync/ai/creation) | ✅ **Cerrado (2026-07-05).** `task_routes` en `celery_app.py` + un worker dedicado por cola en `docker-compose.yml`/`.prod.yml`. Validado en vivo. |
| I-3 | Healthchecks/límites/nginx | ✅ + reforzado (frontend a build de producción resuelve el OOM real que sufrías) |
| I-4 | Logging + Sentry | ✅ |
| I-5 | Cooldown en triggers | ✅ pero con **bug de orden confirmado en el código actual** (ver sección 3.1) |

### 🎨 Frontend/UX
| # | Hallazgo | Estado |
|---|---|---|
| F-1 | Spinner de sync infinito | ✅ |
| F-2 | Estados de error | ✅ |
| F-3 | Accesibilidad (aria-live) | ✅ parcial, no es una auditoría WCAG completa |
| F-4 | Unificar lenguaje visual | ✅ **Cerrado por la sesión #2** (rediseño "midnight" completo). Era L, quedó resuelto. |

### 📦 Producto
| # | Hallazgo | Estado |
|---|---|---|
| P-1 | Aprobador honesto (simulado/real) | ✅ |
| P-2 | Captura completa de reporting | ✅ **Cerrado y validado contra la Graph API real** (2026-07-05). Se reconectó la cuenta `act_992953052009562` con un token nuevo y se corrió un sync FULL + INCREMENTAL reales: 48 adsets, 50 ads, 50 creativos, 25 audiencias y breakdowns (placement/edad/género/dispositivo/región) poblados con datos reales, confirmados vía `GET /reports/brief-data`. En el camino se encontró y arregló un bug real (ver hallazgo 3.8): `customaudiences` pedía un campo (`approximate_count`) que Meta ya no soporta, lo que devolvía 0 audiencias siempre, no por falta de permiso como decía el log. |

---

## 3. Hallazgos NUEVOS de esta revisión (no estaban en la auditoría original)

**3.1 🟡 Bug de orden en `/campaigns/sync-trigger` (confirmado en código, `backend/app/api/v1/campaigns.py:82-101`).**
`enforce_cooldown("sync", ...)` corre **antes** de verificar que existan cuentas
`ACTIVE`. Si la cuenta está `EXPIRED` (que ahora es común gracias a S-6, que marca
`EXPIRED` automáticamente), el usuario recibe "No hay cuentas de anuncios activas
conectadas" (400) **y además** queda bloqueado 60s por el cooldown sin que nada se
haya sincronizado. Fix: mover el `enforce_cooldown` después del chequeo de `accounts`,
o no consumir el cooldown si no hay cuentas. Esfuerzo S.

**3.2 🟢 Duplicación de lectura de `GEMINI_API_KEY`.**
`config.py` (Settings/Pydantic) y `gemini_client.py` (`os.getenv` directo) leen la
misma variable por dos caminos distintos. Menor, pero es exactamente el síntoma de
A-3 (Stack B no consolidado). Se resuelve solo al borrar `gemini_client.py` (ver A-3 arriba).

**3.3 🟡 Cambios de LLM sin tests ni commit.**
El routing OpenAI→Gemini→OpenRouter en `llm.py` (sesión #1) no tiene ningún test
(ni siquiera uno que verifique que con `OPENAI_API_KEY` seteada se elige el `base_url`
correcto), y está **sin commitear**. Riesgo de perderlo si alguien hace `git checkout .`
o similar.

**3.4 🟢 `DEFAULT_AI_MODEL=gpt-5.4-mini` sin validar contra la API real.**
Nunca se hizo una llamada real de prueba a OpenAI con este modelo (el `.env` real
tiene `MOCK_SERVICES=false` pero el mock de LLM se activa por separado — revisar
`OpenRouterClient.analyze_campaigns`: cae a `_generate_mock_narrative` si falla la
llamada real, así que un modelo inválido fallaría en silencio y nadie lo notaría
salvo por logs).

**3.5 🟢 `UPDATE` manual de `organizations.ai_model` en producción/local.**
Forzó `"gpt-5.4-mini"` en todas las organizaciones vía SQL directo, saltándose el
flujo normal de configuración por tenant (`PUT /organizations/my`). No es grave
(es solo un dato), pero rompe el principio de que cada tenant elige su modelo —
documentarlo para no sorprenderse si un tenant "pierde" su preferencia.

**3.6 🟡 Más llamadas a la Graph API por sync (relacionado con I-1/I-2).**
Con P-2.2 + P-2.7, un sync real de una cuenta hace ahora ~12-15 llamadas (antes
~5-8): info de cuenta, campañas, insights de campaña, adsets (estructura + insights),
ads (estructura + insights), adimages, customaudiences, y **5 llamadas más** por los
breakdowns (`_sync_real_breakdowns`, una por dimensión — la Graph API no permite
combinarlas). El jitter de Beat (I-1) sigue mitigando el fan-out entre tenants, pero
el rate limit *por cuenta* (200 calls/hora) hay que vigilarlo con cuentas reales.

**3.7 🟢 Archivos sueltos sin decisión tomada** (detalle en sección 5).

**3.8 🔴 (RESUELTO 2026-07-05) `customaudiences` pedía un campo que ya no existe en la Graph API.**
`_sync_real_inventory` (`backend/app/services/meta_ads/sync.py`) pedía el campo
`approximate_count`, que Meta reemplazó hace tiempo por
`approximate_count_lower_bound`/`approximate_count_upper_bound`. La API real devolvía
`OAuthException code 100 "Tried accessing nonexisting field"`, que el código
interpretaba como **falta de permiso del token** (log: "posible falta de permiso") —
en realidad nunca se sincronizaron audiencias reales para ninguna cuenta, con o sin
permiso. Confirmado y arreglado en esta sesión: tras el fix, la cuenta real pasó de
0 a 25 audiencias sincronizadas. Este bug **solo se pudo encontrar validando contra
la Graph API real** — el fake limiter de los tests nunca lo habría detectado porque
el mock también usaba el nombre de campo viejo (ya corregido también en
`test_audit_fixes.py`).

---

## 4. Qué falta — priorizado para la próxima sesión

Estado al **2026-07-05**: todos los ítems de esta lista quedaron cerrados salvo S-5
(decisión de negocio explícitamente fuera de alcance). Detalle de cada uno:

1. ✅ **Decidir sobre los cambios sin commitear** (routing LLM) — commiteado tal cual
   el 2026-07-05, sin test al principio; el test se agregó después en el punto 6.
2. ✅ **Fix del bug de cooldown** (3.1) — el chequeo de cuentas `ACTIVE` ahora corre
   antes de `enforce_cooldown`.
3. ✅ **Validar P-2 contra Meta real** — hecho con un token nuevo de corta duración
   sobre `act_992953052009562`. Encontró y resolvió un bug real (hallazgo 3.8,
   audiencias). **Nota pendiente:** el token usado no era long-lived; el flujo de
   intercambio a token de 60 días para producción sigue sin implementarse.
4. ✅ **AI-2, cierre real** — el planner resuelve `target_adset_id` desde
   `adset_snapshots` reales (solo si hay exactamente un adset sin ambigüedad para la
   campaña; si no, omite la acción en vez de adivinar). El modifier/rollback eligen
   el nodo correcto según el tipo de acción (adset vs. campaña). Validado con tests
   nuevos. **`ENABLE_META_WRITEBACK` sigue en `false`** — este cierre lo hace
   técnicamente activable con confianza, pero activarlo es una decisión aparte que
   no se tomó en esta sesión.
5. ✅ **A-3** — `master_brief_generator.py` y `gemini_client.py` borrados (código
   muerto confirmado). Resuelve también el hallazgo 3.2 (doble lectura de
   `GEMINI_API_KEY`).
6. ✅ **Tests para sesión #2 y routing LLM** — `_sync_real_breakdowns` y
   `_findings`/`_recommendations` **ya tenían tests desde `efd57c2`** (el hallazgo
   A-6 original estaba desactualizado, se verificó contra el código). Se agregaron
   los tests que sí faltaban: routing OpenAI/Gemini directo/OpenRouter de
   `OpenRouterClient`.
7. ✅ **I-2, colas dedicadas** de Celery — `task_routes` en `celery_app.py` enruta
   sync/ai/creation a colas separadas; `docker-compose.yml` y `.prod.yml` levantan
   un worker por cola. Validado en vivo (sync corrió en `celery-worker-sync`,
   encadenó IA en `celery-worker-ai`).
8. ✅ **D-3, archivado** — en vez de particionado nativo de Postgres (exige migrar
   PKs/constraints de tablas en producción), se implementó archivado periódico:
   `snapshot_archiver.py` mueve a tablas `_archive` (sin FKs) las filas más viejas
   que `SNAPSHOT_RETENTION_DAYS` (180 por defecto) y las borra de la tabla caliente.
   Celery task semanal vía Beat. Validado en vivo.
9. ✅ **AI-4, proveedor LLM** — `OpenRouterClient.provider` (openai/gemini_direct/
   openrouter) se persiste en `AdAccount.narrative_provider` junto a
   `narrative_model`, expuesto en `GET /reports/brief-data`. Validado en vivo:
   `narrative_model=gpt-5.4-mini`, `narrative_provider=openai`.
10. **S-5**: sigue sin tocar — decisión de negocio (deprecar `paso0`/`paso1`), no una
    tarea de código salvo que el usuario lo pida explícitamente.

### 4.1 — Features nuevas pedidas por el usuario (2026-07-05)

- ✅ **Selector de modelo de IA dinámico en Preferencias**: antes mostraba 4 modelos
  fijos de Gemini/Claude vía OpenRouter aunque el backend podía estar enrutando a
  OpenAI o Gemini directo — se podía guardar un modelo que el proveedor activo ni
  soporta. `GET /organizations/ai-models` expone el proveedor realmente activo
  (según qué API key está configurada) y solo sus modelos válidos
  (`MODELS_BY_PROVIDER` en `llm.py`); el frontend puebla el `<select>` con eso y
  muestra qué proveedor detectó. Validado en navegador: con `OPENAI_API_KEY`
  configurada, el selector muestra exactamente gpt-5.4-mini/gpt-5.4/gpt-5.4-nano.
- ✅ **Historial de llamadas a la API de Meta en el dashboard**: `api_call_logs` ya
  se poblaba (vía `rate_limiter.py`) pero no había endpoint ni UI para verlo.
  `GET /campaigns/api-call-history?limit=10` (scopeado por tenant) + tabla al final
  del dashboard principal (fecha/hora, cuenta, método, endpoint, status). Validado
  en navegador con datos reales de la cuenta conectada.

### 4.2 — Limpieza de archivos sueltos (sección 5 de este documento)

- ✅ `informe-rendimiento-jun2026.html` — commiteado, se conserva como referencia.
- ✅ `auditoria2026-opus4.6.md`, `backend/test_db.py` — borrados (ya incorporados/
  redundantes).
- ✅ `auditoria26.md`, `plan.md` — se confirmó y commiteó su borrado.
- ✅ `status.md` — se descartaron los cambios sin commitear (eran una versión más
  vieja/simple que la ya commiteada, confirmado con el usuario antes de tocarlo).

---

## 5. Archivos sueltos — necesitan una decisión del usuario, no tocar solo

- `auditoria2026-opus4.6.md` — un segundo informe de auditoría (otra herramienta/modelo).
  Ya incorporé lo que aportaba de nuevo en este documento. ¿Se conserva como referencia
  histórica o se borra?
- `backend/test_db.py` — script de debug ad-hoc (imprime `cached_narrative` de un
  `AdAccount`). No debería vivir suelto en la raíz de `backend/`; si se quiere
  conservar, mover a algo como `backend/scripts/` con un nombre claro.
- `informe-rendimiento-jun2026.html` — la referencia visual original del reporte
  manual. Sigue siendo útil como vara de comparación para `/reporte`.
- `auditoria26.md` y `plan.md` — aparecen como **borrados sin commitear** desde hace
  varias sesiones. Nadie ha confirmado si el borrado fue intencional. `status.md`
  también tiene cambios sin commitear que parecen revertir a una versión anterior del
  documento — verificar antes de tocar, podría ser trabajo en progreso del usuario.

---

## 6. Cómo correr / verificar

```bash
# Tests del backend (Docker, sin Postgres/Redis reales — SQLite en archivo):
cd backend && ./run_tests.sh -q              # esperado: 63 passed

# Tests del frontend:
cd frontend && npx jest                       # esperado: 3 suites, 7 tests

# Levantar el stack (el backend/frontend NO montan volumen de código —
# un `restart` no alcanza tras cambiar código, hace falta `--build`):
docker compose up -d --build

# Migraciones (si se apaga AUTO_CREATE_TABLES para simular producción):
docker compose run --rm backend alembic upgrade head
```

## ⚠️ Notas operativas vigentes

- **Claves rotadas** (S-1): al desplegar, todos re-loguean; cuentas de Meta con la
  clave Fernet vieja deben reconectarse.
- **`ENABLE_META_WRITEBACK=false`**: no activar hasta cerrar AI-2 (adset real).
- **`AUTO_CREATE_TABLES=true`** en local: recordar que agregar columnas/tablas
  requiere `docker compose up -d --build backend celery-worker`, no solo `restart`.
- Migraciones vigentes: `c3d1e5a9f7b2`, `d5f2b1c8a3e4`, `e6a4c2f1b9d3`, `f1b7d9c4e2a6`
  (esta última crea `adset_snapshots`, `ad_snapshots`, `meta_creatives`, `custom_audiences`).
- El plan detallado de P-2 (ya completo) está en [plan-p2-reporting.md](plan-p2-reporting.md).

## 7. Bugs de discrepancia con Meta (2026-07-05) — 5 fixes relacionados

Síntoma raíz: los datos de la app no coincidían con la plataforma de Meta (ej.
campaña ECQ003 / `120207114665560569`: Meta 137 leads y S/407.90; la app 44 leads
y S/62.27). Diagnosticados con evidencia en DB/logs y verificados contra el código.
Ninguno se detectaba con los tests viejos porque el mock no reproducía la paginación
ni los action types reales de Meta.

**Bug 1 (CRÍTICO) — el sync no seguía la paginación de Meta.**
`MetaAPIRateLimiter.call_api()` hacía UNA request y devolvía `data`, ignorando
`paging.next`. Las llamadas de insights no ponían `limit`, así que Meta devolvía su
página default de 25 filas (las más viejas). Con `last_30d × ~10 campañas ×
time_increment:1` = ~300 filas/día, solo se guardaban 25 → `records_synced=25` exacto
en todos los FULL. Fix: nuevo método `call_api_paginated()` en `rate_limiter.py` que
sigue `paging.cursors.after` y concatena todas las páginas (tope 50, respeta el rate
limiter). Aplicado a insights de campaña/adset/ad + breakdowns + las listas de
estructura (campaigns/adsets/ads/adimages/customaudiences), todas con `limit` alto.
Test: `test_call_api_paginated_concatenates_all_pages` (unidad) y
`test_real_sync_follows_pagination` (fake limiter con 2 páginas, verifica que ambas
fechas se guardan).

**Bug 2 — el conteo de leads no reconocía los de este account.**
`_extract_conversions_conversations` solo tenía `lead`/`fb_pixel_lead`/`lead_grouped`
en la whitelist; los leads de esta cuenta llegan como
`offsite_complete_registration_add_meta_leads` y `offsite_search_add_meta_leads`, que
caían en un fallback difuso (`"lead" in at or "conversion" in at`) capaz de doble-contar
o agarrar de más (ej. `onsite_conversion.post_net_like` matchea "conversion"). Fix:
whitelist explícita `_LEAD_ACTION_TYPES` (incluye los `*_add_meta_leads`, grouped,
fb_pixel_lead, lead, leadgen_grouped) y se eliminó el fallback difuso. Tests:
`test_extract_conversions_whitelist_counts_meta_leads` (calibra 80+57=137) y
`test_extract_conversions_counts_messaging_separately`.

**Bug 3 — fecha off-by-one en el frontend.**
`campaigns/[id]/page.tsx` usaba `new Date(s.date)` con `s.date` date-only
("2026-07-03"), que JS parsea como medianoche UTC → en UTC-5 (Perú) mostraba el día
anterior. Fix: helper `formatSnapshotDate` que parsea como fecha local
(`new Date(\`${d}T00:00:00\`)`). Verificado con `TZ=America/Lima`: antes 2/7, ahora 3/7.
El resto de fechas del front usan timestamp con hora (dashboard/acciones), esas estaban
bien.

**Bug 4 — la cuenta no quedaba EXPIRED de forma consistente ante 190.**
`_maybe_mark_expired` solo se llamaba en el error de la verificación inicial de cuenta;
un `OAuthException code 190` en los insights/estructura/breakdowns no marcaba la cuenta.
Fix: se llama `_maybe_mark_expired` en TODOS los caminos de error (campaigns, insights de
campaña/adset/ad, inventario, breakdowns). Test:
`test_sync_marks_expired_on_190_in_insights`. (El TTL asumido de 60 días del token sigue
como hint blando en `meta_credential.py`; ahora la señal fiable es el 190 real.)

**Bug 5 — presupuesto diario = 0 en la lista de campañas (cuentas ABO).**
`campaign_snapshots.budget` venía 0 porque estas campañas usan presupuesto a nivel ADSET
(ABO), así que el campaign-level `daily_budget/lifetime_budget` viene null. Fix: se
agregó columna `budget` a `AdsetSnapshot` (+ migración Alembic `a1b2c3d4e5f6`), se puebla
en `_sync_real_adsets` desde el `daily_budget/lifetime_budget` del adset, y cuando el
budget de campaña es 0 se setea `campaign.budget = suma de daily_budget de adsets
ACTIVOS`. Test: `test_campaign_budget_derived_from_active_adsets` (50+30, PAUSED
excluido).

**Estado tests:** 55 backend (`./run_tests.sh -q`) + 7 frontend (`npx jest`), todos en
verde.

**⚠️ Pendiente de validación end-to-end contra Meta real:** falta correr un FULL sync
con un token de Meta VIGENTE sobre `act_992953052009562` para confirmar en vivo
(`records_synced` >> 25 ≈ 300, ~30 días continuos, ECQ003 ≈ 137 leads / S/407.90,
presupuesto ya no S/0.00). El token de la cuenta probablemente esté EXPIRED — reconectar
vía Configuración con Graph API Explorer antes de validar. La calibración exacta del
"137" del Bug 2 debe confirmarse con los datos reales una vez arreglada la paginación.

## 8. Aislamiento por cuenta publicitaria (2026-07-06)

Problema: una `Organization` puede tener 2+ `AdAccount` (cuentas de Meta) conectadas,
pero casi todos los endpoints de lectura filtraban solo por `organization_id`,
agregando datos de TODAS las cuentas de la org en la misma respuesta —
`/reports/brief-data` además elegía en silencio "la primera cuenta ACTIVE" en vez de
una elegida por el usuario. Si un usuario conectaba 2+ cuentas a la misma org (ej. dos
negocios distintos), sus campañas/leads/insights/acciones de IA se mezclaban en
dashboard, reporte, diagnósticos y acciones sugeridas.

**Fix:** nuevo concepto de "cuenta publicitaria activa", igual que ya existía
"organización activa" vía `X-Tenant-Slug`:

- Backend: dependencia `get_active_ad_account_id()` en
  [security.py](backend/app/utils/security.py) — lee el header `X-Ad-Account-Id`,
  valida que pertenezca a la organización del usuario (403 si no), auto-selecciona si
  la org tiene exactamente 1 cuenta (retrocompatible, cero cambios de frontend
  necesarios para el caso de hoy), y devuelve `400
  MULTIPLE_AD_ACCOUNTS_NO_SELECTION` si hay 2+ cuentas sin header (en vez de elegir
  "la primera" en silencio, que era el bug). Aplicado a `campaigns.py` (`/recent`,
  `/{id}/history`, `/api-call-history`), `reports.py` (`/brief-data`), `actions.py` y
  `insights.py` (list + approve/dismiss con chequeo de cuenta ajena → 404).
- Frontend: `AdAccountProvider` + hook `useAdAccount()`
  ([AdAccountProvider.tsx](frontend/components/providers/AdAccountProvider.tsx)),
  selector global en el sidebar (oculto si la org tiene ≤1 cuenta), tarjetas
  clickeables en Configuración sincronizadas con el mismo estado. El header se inyecta
  en `apiRequest()` (`lib/api.ts`) leyendo `localStorage["active_ad_account_id"]`.

**Tests:** 8 nuevos en `test_audit_fixes.py` (aislamiento de campañas/historial/reporte/
acciones/insights entre 2 cuentas, retrocompatibilidad de 1 sola cuenta, 400 de
múltiples cuentas sin selección, 403 de cuenta ajena). 63 backend + 7 frontend en
verde.

**Verificado en navegador real** (no solo tests): con la org de prueba (2 cuentas
reales + 1 de prueba agregada temporalmente), cambiar de cuenta en el selector muestra
solo los datos de esa cuenta — probado con los 5 campañas reales y las 30 llamadas de
historial de `act_992953052009562`, sin mezclar con las otras cuentas de la misma
organización.

## 📌 Workflow

Commits directos a `main`, sin PRs (`gh` no está instalado en el entorno).
