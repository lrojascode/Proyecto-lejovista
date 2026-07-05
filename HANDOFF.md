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
| A-3 | Consolidar 2 stacks de Meta | ❌ **Sigue abierto.** Verificado con grep: `master_brief_generator.py` y `gemini_client.py` **ya no los importa nadie** (código muerto — `ReportBuilder` los reemplazó). `meta_api_client.py` + `rate_limiter.py` (el otro) sí se usan (creación de campañas, S-10). Es decir: la consolidación se puede simplificar **borrando directamente** `master_brief_generator.py` y `gemini_client.py` en vez de "migrar" — ya no hacen nada. |
| A-4 | GET de reportes sin efectos | ✅ |
| A-5 | Loop de Celery robusto | ✅ |
| A-6 | Testing | ✅ el núcleo, pero ⚠️ **la funcionalidad agregada en la sesión #2 (breakdowns reales, hallazgos/recomendaciones) no tiene ningún test.** El resumen de esa sesión dice "20 tests backend en verde" — la suite real tiene **33**, sin tests nuevos para esa lógica. No es mentira necesariamente (puede haber corrido un subconjunto), pero **no hay cobertura real de esas ~150 líneas nuevas de negocio**. |

### 🗄️ Base de datos
| # | Hallazgo | Estado |
|---|---|---|
| D-1/D-2 | Unicidad + índice en snapshots | ✅ |
| D-3 | Particionado/archivado | ❌ **Sigue abierto.** Con adset/ad/breakdowns nuevos, hay 3 tablas más creciendo a diario — más urgente que antes. |
| D-4 | Metadatos de narrativa | ✅ (pero ver AI-4 abajo: no registra el *proveedor* LLM) |
| D-5 | `create_all` gateado / Alembic | ✅ en código, pero en la práctica `AUTO_CREATE_TABLES=true` sigue siendo el mecanismo real en local — cuidado, ya nos mordió dos veces (columnas/tablas nuevas no aparecían hasta hacer `--build`, no `restart`). |
| D-6 | FK `ondelete` | ✅ |

### 🤖 Motor de IA
| # | Hallazgo | Estado |
|---|---|---|
| AI-1 | Cifras inventadas | ✅ mock corregido + prompt real con regla anti-alucinación. La sesión #1 (routing LLM) no rompió esto — la regla de sistema sigue en `analyze_campaigns`. |
| AI-2 | Presupuesto real del planner / **resolver adset real antes del write-back** | 🟡 **Parcial.** El planner ya usa presupuesto real (fase 2, hecho hace tiempo). Lo que sigue sin cerrar es que `AIExecutor`/`MetaCampaignModifier` apunten al `adset_id` real — hoy los `planned_api_calls` siguen usando `campaign_id` como target. Ahora que P-2 captura adsets reales, **esto ya es técnicamente resoluble** — es la pieza que falta para poder activar `ENABLE_META_WRITEBACK` con confianza. |
| AI-3 | Histórico insuficiente | ✅ |
| AI-4 | Versionado de prompts | 🟡 `PROMPT_VERSION` existe, pero con el cambio de sesión #1 (multi-proveedor) **no se registra qué proveedor** (OpenAI/Gemini directo/OpenRouter) generó cada narrativa — solo el nombre del modelo (`org.ai_model`), que es ambiguo entre proveedores. Gap nuevo, no estaba en la auditoría original porque el multi-proveedor no existía. |
| AI-5 | Umbrales centralizados | ✅ |
| AI-6 | Flag de write-back | ✅ (`ENABLE_META_WRITEBACK=false`, sigue así — correcto, ver AI-2) |

### ⚙️ Infra
| # | Hallazgo | Estado |
|---|---|---|
| I-1 | Beat cron + jitter | ✅ |
| I-2 | Colas dedicadas (sync/ai/creation) | ❌ **Sigue abierto**, no estaba en el handoff anterior como pendiente explícito pero tampoco se hizo — un solo worker/cola para todo. Con P-2 real ahora haciendo ~12-15 llamadas a Meta por sync (antes ~5-8: cuenta + campañas + insights + adsets + ads + adimages + audiencias + **5 breakdowns nuevos**), esto pesa más. |
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
| P-2 | Captura completa de reporting | ✅ **el núcleo completo** (P-2.1 a P-2.6, esta asistente) **+ P-2.7** (breakdowns reales, hallazgos/recomendaciones, sesión #2). Pendiente real: nunca se validó `_sync_real_data` contra la Graph API **real** — solo contra un fake limiter en tests, y contra la cuenta real el token expiró antes de poder confirmarlo. **Es la validación crítica pendiente**, no una feature nueva. |

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

---

## 4. Qué falta — priorizado para la próxima sesión

1. **Decidir sobre los cambios sin commitear** (routing LLM) — commitear con un test
   mínimo, o completar, o descartar. (S)
2. **Fix del bug de cooldown** (3.1). (S)
3. **Validar P-2 contra Meta real**: conseguir un token vigente (idealmente
   long-lived), reconectar la cuenta, correr un sync real, y confirmar que
   adsets/ads/creativos/audiencias/breakdowns se pueblan de verdad — no solo en tests
   con fake limiter. Esto es lo único que falta para dar por *realmente* cerrado P-2. (S, pero depende de tener el token)
4. **AI-2, cierre real**: resolver `target_adset_id` en el executor/modifier usando
   los `adset_snapshots` ya capturados, para poder considerar activar
   `ENABLE_META_WRITEBACK` con confianza. (M)
5. **A-3, ahora más barato de lo que parecía**: borrar `master_brief_generator.py` y
   `gemini_client.py` (código muerto confirmado) en vez de "migrar" — es casi gratis.
   Lo que sigue vivo de Stack B (`meta_api_client.py`, `rate_limiter.py`,
   `meta_credential_service.py`, `audit_log_service.py`) se queda, ya lo usa
   `campaigns.py`. (S/M)
6. **Tests para lo que agregó la sesión #2**: `_sync_real_breakdowns`, `_findings`,
   `_recommendations`, y el routing LLM de la sesión #1. (M)
7. **I-2, colas dedicadas** de Celery (sync/ai/creation), más relevante ahora que el
   volumen de llamadas por sync subió (3.6). (M)
8. **D-3, particionado**: cada vez más urgente con 3 tablas nuevas creciendo a diario. (M/L)
9. **AI-4, registrar el proveedor LLM** junto a `narrative_model` (para no confundir
   "gpt-5.4-mini vía OpenAI" con "vía OpenRouter"). (S)
10. **S-5**: seguir sin tocar — es una decisión de negocio (deprecar `paso0`/`paso1`),
    no una tarea de código para esta sesión salvo que el usuario lo pida.

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
cd backend && ./run_tests.sh -q              # esperado: 33 passed

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

## 📌 Workflow

Commits directos a `main`, sin PRs (`gh` no está instalado en el entorno).
