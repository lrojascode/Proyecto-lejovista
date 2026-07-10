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
| S-5 | Tokens en prompts manuales (`paso0`/`paso1`) | ✅ **Cerrado (2026-07-06)** — archivos deprecados con banner explícito, apuntan al flujo automatizado de la app. Se conservan como referencia histórica. |
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
10. ✅ **S-5** — **Cerrado (2026-07-06)**, a pedido explícito del usuario: `paso0crearCampañas.md`
    y `paso1DescargarInfoApi.md` marcados como deprecados (banner al inicio de cada
    archivo) — apuntan al flujo automatizado de la app (`/campaigns/new`, `/reporte`) en
    vez de pegar un token real en una sesión de chat. Se conservan como referencia
    histórica de las reglas de negocio ya incorporadas al código; no se borraron.

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

## 9. Creación de campañas — 6 fases + 3 bugs encontrados depurando en vivo (2026-07-06)

Ver [PLAN-CREACION-CAMPANAS.md](PLAN-CREACION-CAMPANAS.md) para el detalle completo de
las 6 fases (Saga sobre stack real, persistencia de `CampaignDraft`, PAUSED/naming por
marca/anti-bloqueo, motor de sugerencia 3A+3B+3C con LLM, descubrimiento de página+WhatsApp,
UI, video/UGC). Resumen de lo que quedó pendiente ahí, más 3 bugs reales encontrados
depurando contra la cuenta real de Agencia Kibo (`act_1317785970189038`) después del
"listo": botón de entrada nunca cableado en la lista de Campañas, WhatsApp vacío por falta
del permiso `whatsapp_business_management` del token (Meta omite el campo sin error — mismo
patrón traicionero que el bug de `customaudiences`/`approximate_count`), y un bug de
multi-cuenta real en `CampaignValidator` (resolvía el token de Meta por `organization_id`
sin considerar qué `AdAccount` específica, rompiendo con `MultipleResultsFound` en cuanto la
org tenía 2+ cuentas — cerraba, de paso, el resto de la duplicación A-3 para este flujo).

**Sigue pendiente, sin cerrar:**
- Validación end-to-end completa (los 4 objetos creados en Meta real) — bloqueada ahora
  mismo por el spend cap/fondos de Kibo; ITS IN tiene el token expirado y está en fase de
  testing (no usar por ahora).
- Permiso `whatsapp_business_management` en el token, si se quiere que el descubrimiento
  automático de WhatsApp funcione sin el fallback manual.

**Cerrado en esta misma sesión (2026-07-06), a pedido del usuario:**
- ✅ Soft-delete de cuenta publicitaria: `AdAccount.status="ARCHIVED"` vía
  `POST /organizations/my/ad-accounts/{id}/archive` (+ `/restore`), excluida de
  `list_my_ad_accounts` por defecto, de `get_active_ad_account_id` y de la creación de
  campañas. UI en Configuración con confirmación antes de archivar. 7 tests nuevos.
- ✅ Copy del anuncio ganador real: `_sync_real_ads` ahora captura
  `creative{title,body,call_to_action_type}` (antes solo `image_hash`); nuevas columnas
  `creative_title/body/cta_type` en `ad_snapshots` (migración `f2b3c4d5e6a7`).
  `CampaignSuggester` expone `winning_ad_copy` y `OpenRouterClient.suggest_campaign_copy`
  lo usa como inspiración fuerte (puede reutilizar frases/ganchos reales, no solo tono).
  6 tests nuevos.
- ✅ S-5 — ver arriba, archivos deprecados con banner.

## 10. ✅ Eliminar/archivar cuenta publicitaria (implementado 2026-07-06)

Planteado por el usuario (2026-07-06) a raíz de tener cuentas de prueba conectadas
(Agencia Kibo, ITS IN) sin forma de desconectarlas. Hoy no existe ningún endpoint ni UI
para eliminar un `AdAccount` — análisis de qué implicaría antes de construirlo:

**Cascada de borrado (revisado en el código, `ondelete` de cada FK a `ad_accounts.id`):**
- ✅ `CASCADE` automático: `campaign_snapshots`, `adset_snapshots`, `ad_snapshots`,
  `ai_insights`, `ai_actions`, `campaigns`, `meta_creatives`, `custom_audiences`,
  `tenant_api_quotas`, `sync_jobs` — se borran solos con la cuenta, sin acción extra.
- ⚠️ **`api_call_logs` NO tiene `ondelete` configurado** (`app/models/api_call_log.py:11`,
  FK sin cláusula → Postgres exige `RESTRICT` por defecto). Borrar una `AdAccount` con
  cualquier llamada registrada (o sea, prácticamente todas) **fallaría con un error de
  integridad referencial** tal como está el esquema hoy. Antes de exponer un "eliminar
  cuenta" hay que decidir: (a) migración para poner `ondelete=CASCADE` también ahí
  (se pierde el historial de auditoría de llamadas a Meta de esa cuenta), o (b) borrar
  explícitamente los `api_call_logs` de esa cuenta en el mismo endpoint antes del delete
  (conserva la posibilidad de archivarlos primero si se quisiera).
- `MetaCredential` no cuelga de `AdAccount` sino al revés (`AdAccount.meta_credential_id`
  apunta a `MetaCredential`, con `ondelete="SET NULL"") — el credential queda huérfano si
  no se borra aparte; revisar si conviene borrarlo también o dejarlo (podría reusarse si
  se reconecta la misma cuenta).

**Riesgo de negocio, no solo técnico:**
- Es **irreversible**: se pierde todo el historial de rendimiento (snapshots, insights,
  acciones de IA) de esa cuenta — no hay "papelera" ni soft-delete hoy en el modelo.
- Si la cuenta activa eliminada era la última/única de la organización, el frontend
  (`AdAccountProvider`) queda sin cuenta seleccionada — hay que decidir el fallback (¿pantalla
  de "conecta una cuenta"? ¿redirigir a Configuración?).
- Acción "hard-to-reverse" según las reglas de seguridad del proyecto: si se implementa,
  debería pedir confirmación explícita (ej. escribir el nombre de la cuenta) antes de
  ejecutar, igual que otras acciones destructivas.

**Alternativa más segura, a considerar primero:** en vez de un "eliminar" real, un
`AdAccount.status = "ARCHIVED"` (soft-delete) que la oculte de dropdowns/selectores sin
borrar nada — reversible, no toca el problema del `api_call_logs` FK, y ya existe el
campo `status` en el modelo. Más barato de construir y sin el riesgo de pérdida de datos.

**Implementado con la alternativa más segura propuesta abajo (soft-delete, no borrado real):**
`POST /organizations/my/ad-accounts/{id}/archive` marca `status=ARCHIVED` (y `/restore`
revierte). Excluida por defecto de `list_my_ad_accounts` (`include_archived=true` para
verla en Configuración), de `get_active_ad_account_id` (no seleccionable ni cuenta para
"elige una cuenta") y de la creación de campañas. Nada se borra — el historial
(snapshots, insights, acciones) queda intacto y la cuenta se puede restaurar en cualquier
momento. UI en Configuración: tarjeta atenuada con badge "Archivada" + botón "Restaurar";
las activas piden confirmación inline ("¿Archivar X?") antes de archivar. 7 tests nuevos
(`test_ad_account_archive.py`).

## 11. Riesgo previo, IA explicable + aprendizaje, y contador de tokens (2026-07-09)

Tres ajustes pedidos por el usuario sobre el flujo de creación de campañas:

### 11.1 — Nivel de riesgo ANTES de crear (Ajuste 1)
El riesgo dejó de ser la constante fija `CAMPAIGN_CREATION_RISK_LEVEL="MEDIUM"` y ahora
se calcula dinámicamente en `app/services/campaign_risk.py::assess_campaign_risk`
(presupuesto vs. lo sugerido por el histórico, presupuesto absoluto, estado de la cuenta,
y términos que Meta suele rechazar en el copy → LOW/MEDIUM/HIGH + `reasons[]`). Nuevo
endpoint `POST /campaigns/risk-preview` recalcula el riesgo con los valores actuales del
formulario. En el frontend (`campaigns/new`) el botón ahora dice "Revisar riesgo y crear
campaña": al pulsarlo se muestra el nivel de riesgo + razones y recién ahí aparece el
botón de confirmación "Entiendo el riesgo — Crear Campaña (PAUSADA)". El riesgo también
se muestra a modo informativo en el panel de sugerencia. Tests: `test_campaign_risk.py`.

### 11.2 — IA explicable + historial de entrenamiento (Ajuste 2, Fase A)
`app/services/campaign_learning.py` añade dos piezas deterministas (sin LLM, sin inventar
cifras): `assess_maturity` (mira el histórico y decide modo EXPLORACIÓN vs. OPTIMIZADO —
cuando el CPL de ≥3 campañas ganadoras es estable, la IA "convergió" y replica lo que
funciona sin experimentar) y `build_analysis` (arma el objeto explicable: summary,
fortalezas, debilidades, cambios propuestos con su porqué, fallos pasados aprendidos, días
de evaluación, confianza). `CampaignSuggester.suggest` ahora devuelve `maturity` +
`analysis`; se muestran en un panel "🧠 Qué está haciendo la IA" en `campaigns/new`. Cada
creación persiste una fila en `campaign_learning_logs` (modelo nuevo) con params + análisis
+ madurez + riesgo → es la memoria de entrenamiento. Endpoint `GET /campaigns/learning-history`.
**Fase B pendiente** (no hecha): job de Celery Beat que, pasados los días de evaluación,
re-lea las métricas reales de la campaña y rellene `outcome_json` para cerrar el ciclo.
Tests: `test_campaign_learning.py`.

**Conexión IA→creación (targeting optimizado)**: antes `task_create_adset` hardcodeaba el
público (edad 25‑65, sin dispositivo, placements fijos), así que las optimizaciones que la
IA muestra NO se aplicaban a la campaña real. Nuevo `app/services/campaign_targeting.py::
build_targeting` traduce `best_age_range`/`best_device`/`excluded_placements` del suggester
al `targeting` de Meta (edad del bucket ganador; dispositivo → user_os/device_platforms a
nivel OS, no modelo exacto, para no matar alcance; placements = allowlist menos los
excluidos, sin quitar los requeridos). `POST /campaigns/` inyecta estas señales en el
`initial_context` de la Saga, y el path de acción sugerida (`planner.py`) usa el mismo
helper. Fallback a defaults (25‑65, sin device) si no hay histórico. Verificado E2E: el
adset enviado a `/adsets` para la cuenta ITS IN sale con age_min 18/age_max 24 +
user_os Android + destination_type WHATSAPP + promoted_object con el WhatsApp. **La GEO se
deja aparte**: mapear "zona" de texto a región/radio de Meta es decisión de producto (hoy
la zona solo va en el nombre; el geo real es la región del tenant). Tests:
`test_campaign_targeting.py` (9).

### 11.3 — Contador de tokens por funcionalidad + costo (Ajuste 3)
`app/services/ai/token_tracking.py` captura el bloque `usage` de cada llamada REAL al LLM
(antes se descartaba) y lo persiste en `ai_token_usage` (modelo nuevo), etiquetado por
`feature` (`master_brief`, `campaign_copy`, `campaign_copy_action`). Registra
prompt/completion/reasoning/total tokens, `used_cot` (True si `reasoning_tokens>0`) y
`estimated_cost_usd` (tabla de precios `MODEL_PRICING` por modelo). Usa su propia sesión de
BD y traga excepciones — medir jamás rompe la llamada real. Los tres call-sites del LLM
(`engine.analyze_campaigns`, `campaigns.get_campaign_suggestion`,
`planner.suggest_campaign_copy`) pasan un `UsageContext`. Endpoint `GET /ai-usage?days=N`
agrega por funcionalidad. Nueva página frontend `/ai-usage` (nav "Uso de IA") con totales +
tabla por funcionalidad. En MOCK/sin API key no se registra nada. Tests: `test_token_tracking.py`.

Migraciones: `a3b8c1d4e7f2` (ai_token_usage) y `b4c9d2e5f8a3` (campaign_learning_logs).
153 tests del backend en verde. Rebuild completo del stack aplicado.

## 12. Histórico de inversión mensual, subida de imagen local, y políticas por rubro (2026-07-10)

### 12.1 — Histórico de inversión mensual (sin desglose por campaña)

Nueva tabla `ad_account_spend_monthly` (`ad_account_id`, `year_month` "YYYY-MM", `spend`,
`closed`) — un total por cuenta y mes, no por campaña. `MetaAdsSyncService._sync_spend_history`
(`app/services/meta_ads/sync.py`) hace **backfill una sola vez por cuenta**: si no hay
ninguna fila para la cuenta, pide `{account}/insights` con `time_increment=monthly` +
`date_preset=maximum` (una fila por mes calendario, barato incluso para cuentas de años).
En cualquier sync posterior (ya hay backfill), solo pide el mes en curso
(`date_preset=this_month`, un total) y cierra perezosamente (`closed=True`) cualquier mes
viejo que quedara abierto — **nunca vuelve a bajar el histórico completo**. Mock
(`_sync_mock_spend_history`) genera 18 meses ficticios una sola vez, para poder probar la UI
sin cuenta real. Endpoint `GET /reports/spend-history` (scopeado por `get_active_ad_account_id`,
mismo patrón que `/brief-data`). Frontend: card "Histórico de Inversión Mensual" en el
dashboard (`page.tsx`) con `LineChart` de `recharts`. Tests: `test_spend_history.py` (6).
Migración: `c8d3e5f1a9b4`.

### 12.2 — Subir imagen a la app en vez de pegar una URL

`POST /campaigns/upload-image` (multipart, JPG/PNG/WebP, máx 8MB) guarda el archivo en
`MEDIA_ROOT/{organization_id}/{uuid}.{ext}` y devuelve `image_path`. En vez de que el worker
de creación vuelva a descargar la imagen por HTTP (que en dev rompería porque `api.localhost`
no resuelve dentro de la red de Docker), `MetaAssetRepository.get_or_upload_image` acepta un
nuevo parámetro `image_path` y **lee el archivo directo del disco compartido** —
`backend`/`celery-worker-*` ya comparten el bind mount `./backend:/app` en dev; en prod se
agregó el volumen nombrado `media_data` a `backend` y `celery-worker-creation` en
`docker-compose.prod.yml`. Precedencia en el pipeline: `image_hash` > `image_path` >
`image_url` (compat retro, `image_url` se mantiene por si se necesita a futuro). Frontend:
`campaigns/new/page.tsx` reemplaza el input de texto "URL de imagen" por un
`<input type="file">` con preview instantáneo client-side (`URL.createObjectURL`, sin
round-trip) y subida en background; el campo "Image Hash" queda como opción avanzada para
reutilizar una imagen ya subida a Meta. Tests: `test_campaign_image_upload.py` (6, incluye
rechazo de path traversal). Verificado end-to-end en Docker: archivo subido vía UI aparece en
`/app/media/{org_id}/` dentro del contenedor `backend`.

### 12.3 — Políticas de Meta relevantes según el rubro del negocio

Decisión (confirmada con el usuario): selector manual de rubro en Configuración, **sin IA**
— las políticas de Meta son un tema de cumplimiento real y el motor ya tiene la regla de
nunca inventar/asumir hechos (AI-1); usar un LLM para resumir/citar políticas es más riesgoso
que para sugerencias de campaña. `Organization.industry` (nullable, migración `d2f6a8c3b1e7`)
+ catálogo curado a mano en `app/services/policy_catalog.py` (`INDUSTRIES`, ~11 rubros;
`GENERIC_POLICIES`, 5 recordatorios aplicables a cualquier rubro; `INDUSTRY_POLICIES`, 1-2
puntos específicos por rubro). Endpoint `GET /organizations/my/policies` devuelve
`{industry, policies, available_industries}` — el mismo call sirve para poblar el selector de
Configuración y la card del dashboard, sin un segundo endpoint. El contenido son recordatorios
en lenguaje propio, no citas literales de Meta; el dashboard siempre enlaza a
`facebook.com/policies/ads/` para el texto oficial. Frontend: `<select>` "Rubro del negocio"
en `settings/page.tsx` (mismo patrón que el selector de modelo de IA); card "Políticas de Meta
relevantes para tu negocio" en el dashboard (`page.tsx`), con nota + link a Configuración si
no hay rubro elegido. Tests: `test_business_policies.py` (7).

**Nota operativa:** `AUTO_CREATE_TABLES=true` (dev) crea tablas nuevas automáticamente pero
**no altera tablas existentes** — la columna `organizations.industry` no aparece sola.
Se aplicó manualmente en la BD local (`ALTER TABLE organizations ADD COLUMN industry
VARCHAR;`) para esta sesión; en producción, correr `alembic upgrade head` normalmente sí la
crea (parte de la cadena de migraciones).

181 tests del backend en verde (162 + 19 nuevos). Verificado en vivo en Docker (login
admin@seed.com, cuenta ITS IN): histórico mensual renderiza con datos de prueba insertados y
removidos tras verificar, subida de imagen real confirmada en disco del contenedor, selector
de rubro persiste y la card de políticas cambia acorde (probado con `telecom_cable` y
revertido a `NULL` tras la prueba, para no dejar un rubro inventado en la cuenta real).

## 📌 Workflow

Commits directos a `main`, sin PRs (`gh` no está instalado en el entorno).

---

## Fase 0 del plan de auditoría — COMPLETA (2026-07-10)

Ver `plan-implementacion-auditoria.md` (plan completo por fases) y `auditoria2026.md`.
Los 10 quick wins, un commit por paso:

1. **B-1** `0e9d24f` — dedupe de insights: cada análisis descarta los previos de la
   cuenta antes de insertar; se acabaron las alertas repetidas en /diagnostics.
2. **E-1** `e1b6d1a` — `/campaigns/learning`: timeline del aprendizaje de la IA
   (modo, riesgo, razonamiento, bloque de outcome listo para la Fase 1).
3. **D-3** `a0f1b2e` — creación de campañas: escucha el WS `campaign_creation`,
   polling 40→120 intentos (la Saga tarda ~3 min y el timeout viejo era ~100s).
4. **A-2** `69abd96` — borrado el sync fantasma (sync_tasks/meta_sync_service/
   meta_insights_service/router /sync). **Bonus crítico:** `app/workers/__init__.py`
   creaba una 2ª app de Celery sin beat_schedule que ganaba `-A app.workers` — el
   Beat corría vacío (el sync de 6am y el análisis de 7am no se programaban).
   Verificar en logs del beat que el schedule dispare.
5. **D-1** `84535b6` — debounce del análisis post-sync (lock Redis SET NX EX 360 +
   countdown 300): N cuentas → 1 análisis, no N (ni N² llamadas al LLM).
6. **A-5** `8574a32` — `META_API_VERSION` en settings, v19.0 (EXPIRADA, devolvía
   error) → v25.0. Pendiente: FULL sync real end-to-end (checklist 2.3 del plan).
7. **C-4** `f3c70df` — fallback del LLM honesto: `narrative_provider=
   "fallback_template"` + badge "Informe básico — IA no disponible" en la UI.
8. **E-2** `8cffb1f` — métrica primaria unificada ("Conversaciones", mismo criterio
   conversations-or-conversions que ReportBuilder) y ventanas rotuladas (7d vs 30d).
9. **D-2** `31420cc` — healthchecks en compose de prod + `GET /health/deep` (último
   sync exitoso; >24h => degraded) para uptime-monitor externo.
10. **A-3** `fe3c7e9` — diagnóstico afinado vs. la auditoría: el modifier SÍ capturaba
    backup_state, pero la mutación in-place del JSON no se persistía (SQLAlchemy no
    detecta mutaciones internas sin MutableDict). Fix: reasignar el dict.

Estado tests al cierre: **192 backend + 12 frontend en verde**. Stack local verificado
corriendo (backend healthy, workers ready, beat con schedule real).

Siguiente: **Fase 1** (outcome loop del aprendizaje, C-1) y **Fase 2** (unificar
creación en la Saga + write-back piloto) — pueden ir en paralelo.

## Fase 1 del plan de auditoría — COMPLETA (2026-07-10)

El ciclo de aprendizaje de la IA está cerrado: cada campaña que crea queda con
hipótesis registrada, se evalúa sola contra los snapshots reales al vencer su
ventana, y el veredicto alimenta las decisiones siguientes y la UI.

- `3bed628` **1.1 Outcome loop (C-1)**: `services/campaign_outcome.py` +
  task Beat diaria `evaluate_campaign_outcomes` (8:00 Lima, cola `ai`).
  Veredicto determinista: WIN (costo/conv ≤ esperado+20%), LOSS (gastó sin
  conversaciones o >2× esperado), NEUTRAL, NOT_ACTIVATED (Saga fallida/sin
  actividad). `outcome_json` guarda cifras auditables + hipótesis original.
- `30033c5` **1.2 Madurez con resultados propios**: convergencia exige ≥2 WIN
  propios además del CV estable; 2 LOSS recientes fuerzan exploración.
  `build_analysis` expone el `track_record` en el summary.
- `b8ad7b3` **1.3 UI**: OutcomeBlock muestra veredicto (incl. NOT_ACTIVATED),
  cifras y razón.

Estado: 208 tests backend + 14 frontend en verde. Beat y worker `ai`
reiniciados en local con el schedule nuevo (verificado: `daily-campaign-outcomes`
cargado, ruta a cola `ai`). El dev DB no tiene learning logs aún — el loop se
verá con las próximas campañas creadas (o reduciendo `evaluation_window_days`
a 0 en staging para probar el flujo completo, ver plan 1.3).

Siguiente: **Fase 2** (unificar CREATE_CAMPAIGN en la Saga + MODIFY_PLACEMENTS
válido + checklist de write-back piloto, ver plan 2.1–2.3).

## Fase 2 del plan de auditoría — COMPLETA (2026-07-10)

Un solo camino de código para crear campañas (aprobadas por la IA o por
formulario manual) y el write-back real ya tiene un candado por cuenta.

- `e363bff` **2.1 Unificar CREATE_CAMPAIGN en la Saga (A-1)**: el executor ya
  no tiene un flujo inline duplicado — despacha el mismo chain de
  `creation_tasks` que usa `/campaigns/`. Si la página/WhatsApp de destino o
  el creativo no se resuelven sin ambigüedad, la acción queda `NEEDS_INPUT`
  (visible en `/actions`) en vez de adivinar.
- `9b067f9` **2.2 MODIFY_PLACEMENTS válido (A-4)**: el payload real a Meta
  ahora separa `publisher_platforms` (red) de `facebook_positions`
  (posición) — antes se mezclaban y Meta lo habría rechazado. `old_placements`
  sale del `targeting_summary` real del adset, no de una lista hardcodeada.
- `8fbf988` **2.3 Allowlist de write-back (F-2)**: `WRITEBACK_ACCOUNT_ALLOWLIST`
  permite encender `ENABLE_META_WRITEBACK` solo para la cuenta piloto en vez
  de para todo el parque de un golpe.

### Checklist operativo para encender el write-back en la cuenta piloto

Pendiente de ejecución manual (no es código, es una secuencia de validación
contra Meta real) antes de aprobar escrituras en más cuentas:

1. **Validar un FULL sync end-to-end** contra Meta real con la API v25.0
   (pendiente desde el HANDOFF de la Fase 0) — confirmar que campañas,
   adsets, ads y breakdowns sincronizan sin error de versión.
2. En `.env` de producción: `ENABLE_META_WRITEBACK=true` +
   `WRITEBACK_ACCOUNT_ALLOWLIST=<id-de-la-cuenta-piloto>` (el `AdAccount.id`
   interno, no el `external_account_id` de Meta — verificar con
   `SELECT id FROM ad_accounts WHERE ...`).
3. Secuencia de prueba en la cuenta piloto:
   - Crear una campaña desde la UI (o aprobar una acción `CREATE_CAMPAIGN`
     de la IA) → verificar en Ads Manager que existe, **PAUSADA**, con el
     targeting correcto (edad/dispositivo/placements).
   - Aprobar un `MODIFY_BUDGET` → verificar el cambio de presupuesto real.
   - Aprobar un `MODIFY_PLACEMENTS` → verificar en Ads Manager que
     `facebook_positions` cambió y que Instagram nunca aparece.
   - Forzar un fallo (token inválido o similar) → verificar que el rollback
     automático del executor restaura el `backup_state` (persistido, A-3).
4. Auditar cada paso vía el historial de llamadas API del dashboard
   (`APICallLog`, ya expuesto en Settings/Actividad técnica).
5. Solo tras firmar este checklist, ampliar `WRITEBACK_ACCOUNT_ALLOWLIST`
   a más cuentas (o vaciarlo para el parque completo).

Estado: 225 tests backend en verde. Stack local reiniciado y sano tras los
cambios (backend + 3 workers healthy).

Siguiente: **Fase 3** (clasificador único de campañas + prompt v2, C-2/C-3,
puede ir en paralelo si hay dos personas) o **Fase 4** (rendimiento de datos,
recomendado antes de sumar más cuentas reales).

## Fase 3 del plan de auditoría — COMPLETA (2026-07-10)

Un solo clasificador de campañas para todo el producto, el Master Brief
enriquecido con contexto real y comparativas, y el copy generado por IA con
variantes que el usuario elige (en vez de un texto único sin alternativa).

- `3d1744e` **3.1-3.3 Clasificador único + Master Brief v2 (C-2, A-6, C-3)**:
  `thresholds.classify_campaign` reemplaza los 3 umbrales hardcodeados que
  existían (report_builder.py, narrativa mock, HTML brief) por uno relativo
  al target_cpl dinámico de la cuenta. Se elimina `generate_html_brief`
  (~300 líneas muertas). El prompt real y la plantilla mock del Master Brief
  comparten contrato de salida (5 secciones fijas) y contexto enriquecido:
  target_cpl, KPIs 7d vs 7d previos con Δ%, top/bottom campañas, pacing.
- `9b067f9`… ya cerrado en Fase 2.
- `4795507` **3.4 Copy v2 (anexo 6.2)**: el LLM genera 2 variantes de
  headline/primary_text; el usuario elige en `/campaigns/new`. Límites de
  Meta (headline ≤40, description ≤30) instruidos al LLM y validados en
  `CampaignValidator`. `response_format: json_object` para reducir fallback
  por JSON mal formado. Rubro de la organización (`industry`) inyectado en
  el prompt.

**Nota de alcance** (transparencia, como A-3 en la Fase 0): la clasificación
de campañas de `campaigns/page.tsx` (tabla del dashboard) sigue con su
propio criterio client-side independiente — quedó fuera de este alcance y
se señalizó como tarea de seguimiento (chip de sesión) en vez de ampliarse
aquí sin que el usuario lo pidiera.

Estado: 251 tests backend + 14 frontend en verde. Verificado en vivo: el
selector de variantes de copy funciona end-to-end contra el stack real
(requirió reconstruir la imagen de `frontend`, que no tiene hot-reload).

Siguiente: **Fase 4** (rendimiento de datos: agregaciones SQL, cache del
ReportBuilder, KPIs agregados en el dashboard — recomendada antes de sumar
más cuentas reales) o **Fase 5** (producto premium: dashboard rejerarquizado,
comparativa temporal, PDF real, pulido UX, tipado de frontend).
