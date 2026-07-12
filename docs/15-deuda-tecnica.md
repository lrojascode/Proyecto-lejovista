# 15 · Deuda Técnica y Auditoría

Hallazgos identificados **recorriendo el código actual**. Cada uno indica gravedad, explicación, impacto y propuesta. La gravedad es una valoración de esta documentación, no del equipo.

Escala: 🔴 Alta · 🟠 Media · 🟡 Baja.

> Nota: el repo ya pasó por una auditoría previa (ver `HANDOFF.md`); muchos bugs históricos (`A-*`, `B-*`, `C-*`, `D-*`, `S-*`) **ya están corregidos** y anclados con tests. Lo que sigue es lo que **queda pendiente o es mejorable** en el estado actual.

## 1. Seguridad

### ✅ S-1 · Sin control de acceso por rol (RBAC) — RESUELTO
- **Explicación (histórica)**: `OrganizationUser.role` (`ADMIN`/`OPERATOR`/`VIEWER`) se guardaba y se exponía, pero ningún endpoint lo verificaba. Un `VIEWER` podía aprobar acciones, crear campañas o conectar cuentas.
- **Resolución**: dependencia `require_role(min_role)` en `app/utils/security.py` (jerarquía `ADMIN > OPERATOR > VIEWER`, reconsulta la BD en cada request). Aplicada a los ~12 endpoints de escritura de organizaciones/cuentas, campañas, insights y acciones — ver la tabla en [08-autenticacion §7](08-autenticacion.md). Tests en `backend/tests/test_rbac.py` (jerarquía en ambos sentidos + membresía faltante).
- **Hallazgo relacionado descubierto al implementar**: `POST /auth/signup` vinculaba como `ADMIN` a cualquiera que se registrara con el slug de una organización **ya existente** (sin invitación, sin verificación) — el propio flujo de signup era una vía de escalada de privilegios que habría dejado el RBAC recién construido sin efecto. Corregido: signup ahora solo puede fundar una organización nueva; devuelve 400 si el slug ya existe.

### ✅ S-2 · Tokens JWT en `localStorage` — RESUELTO
- **Explicación (histórica)**: `access`/`refresh` vivían en `localStorage`, accesibles a cualquier script (XSS).
- **Resolución**: el access token pasó a vivir en memoria del módulo `frontend/lib/api.ts` (nunca persistido); el refresh token viaja **solo** como cookie `httpOnly`+`SameSite=Lax` (+`Secure` si HTTPS) que fija el backend (`set_refresh_cookie`, `app/utils/security.py`) y nunca aparece en el cuerpo JSON. Detalle en [08-autenticacion §2](08-autenticacion.md) y [09-seguridad §9c](09-seguridad.md).
- **Bug real descubierto e corregido al implementar**: el guard del dashboard (`(dashboard)/layout.tsx`) siempre forzaba un refresh contra la cookie al montar, y la navegación post-login usaba `window.location` (recarga completa) — entre ambas cosas, el access token recién obtenido en login/signup se descartaba de inmediato y la sesión dependía SIEMPRE del refresh por cookie, incluso cuando ya había un token válido en memoria. Corregido: login/signup navegan con el router de Next (preserva memoria) y el guard solo refresca por cookie si no hay token ya en memoria.
- **Limitación conocida y documentada** (no bloqueante): en desarrollo local con subdominios `*.localhost`, Chrome no las trata como el mismo "site" para `SameSite`, así que un reload de página en ese modo específico pierde la sesión (no ocurre en producción con dominio real ni en modo túnel). Ver [08-autenticacion](08-autenticacion.md) para el workaround.
- CSP estricta **no** se implementó en este cambio (evaluado, pospuesto — ver nota de esfuerzo/riesgo en [22-plan-remediacion-deuda](22-plan-remediacion-deuda.md)); la mitigación principal (cookies httpOnly) ya reduce el impacto de un XSS a robar solo el access token de vida corta, no la sesión completa de 7 días.

### ✅ S-3 · Blocklist de JWT fail-open — RESUELTO
- **Explicación (histórica)**: `is_revoked` devolvía `False` si Redis no respondía. Un refresh revocado podría aceptarse durante una caída de Redis.
- **Resolución**: `is_revoked` ahora es fail-**closed** — levanta `RevocationCheckUnavailable` si Redis no responde, y `POST /auth/refresh` responde `503` en vez de aceptar la renovación a ciegas. Alcance acotado a propósito: solo la lectura de la blocklist (`is_revoked`); la escritura (`revoke_jti`, en logout/rotación) sigue best-effort/fail-open. El resto de la capa Redis (rate limiting, cooldown, cache) también sigue fail-open. Ver [09-seguridad §9d](09-seguridad.md).
- **Hallazgo real durante la implementación**: varios tests de `/auth/refresh` corrían con Redis genuinamente inalcanzable en el entorno de test — el fail-open anterior lo ocultaba en silencio. Se agregó un fake Redis en memoria por test (mismo patrón que `tests/test_throttle.py`) donde la revocación real importaba para la aserción.

### ✅ S-4 · `X-Forwarded-For` sin lista de proxies confiables — RESUELTO
- **Explicación (histórica)**: `get_client_ip` tomaba el primer valor de `X-Forwarded-For` incondicionalmente; era spoofeable si algo distinto de Nginx llegaba al backend.
- **Resolución**: solo se confía en el header si el peer directo (`request.client.host`) es una IP privada/loopback — el esquema de red del deployment (backend con `expose`, no `ports`, solo alcanzable por Nginx) garantiza que ese sea normalmente el caso. Si el peer directo fuera público, se ignora el header. Tests en `backend/tests/test_client_ip.py`.

### ✅ S-5 · Prompt injection desde datos de Meta — RESUELTO
- **Explicación (histórica)**: nombres de campaña y copy reales se inyectaban en prompts del LLM como texto plano, sin delimitar. El system prompt mitigaba la alucinación numérica pero no había defensa específica anti-inyección.
- **Resolución**: el texto que se interpola como texto libre (nombre de org/cuenta, `product_name`, nombre/copy de la campaña ganadora) se envuelve en delimitadores explícitos (`_delimit_untrusted`, `app/services/ai/llm.py`) y el `system_prompt` instruye tratar ese contenido siempre como dato, nunca como instrucción. Los datos que ya viajaban como `json.dumps(...)` (campañas, KPIs, segmentación) no se tocaron — el escapado de JSON ya los protegía estructuralmente. Ver [09-seguridad §9f](09-seguridad.md).

## 2. Arquitectura y mantenibilidad

### ✅ M-1 · Dos clientes de Meta y dos rate limiters coexistiendo — RESUELTO
- **Explicación (histórica)**: `MetaAPIRateLimiter` (usado en todo el flujo real) convivía con `MetaApiClient`+`services/rate_limiter.py`+`meta_credential_service.py` (alternativo, "apenas usado").
- **Resolución**: se confirmó con un grep exhaustivo (`from app.services.meta_api_client import`, etc.) que el segundo grupo no tenía **ningún** llamador fuera de sí mismo — ni en `app/`, ni en `tests/`. Se eliminaron los 3 archivos completos, junto con el modelo huérfano `TenantApiQuota` (tabla `tenant_api_quotas`, sin lector/escritor real tampoco) — migración `a4f8c2d6e9b1` (drop de la tabla, con downgrade fiel). Cobertura del backend subió de 84% a 86% solo por quitar código muerto sin cubrir. Ver [11-servicios-externos §1](11-servicios-externos.md).
- **Hallazgo relacionado, no resuelto en este cambio**: `services/audit_log_service.py` (y el modelo `ActionLog` que solo él escribe) resultó ser código muerto de la misma familia — sin llamadores vivos. Se deja para [M-3](15-deuda-tecnica.md) por ser la misma categoría de hallazgo (modelo/servicio marginal), no por el cliente de Meta.

### ✅ M-2 · Reglas de negocio globales presentadas como "por tenant" — RESUELTO
- **Explicación (histórica)**: `TENANT_CAMPAIGN_RULES` (geo Lima, objetivo, placements) y `AIThresholds` eran **globales**, con TODOs para moverlas a tabla por tenant. El país/moneda (`PEN`/Perú) sigue como default razonable en varios lugares (engine, targeting, modifier con key `2771`) — eso no cambió, lo que cambió es que ahora es *personalizable*.
- **Resolución**: dos columnas JSON en `Organization` (`campaign_rules_overrides`, `ai_thresholds_overrides`; default `{}`; migración `e3f7a2c8d4b9`) en vez de una tabla separada — la relación es 1:1 con la org, así que un JOIN extra no habría aportado nada. `get_thresholds_for_org(organization)` (`app/services/ai/thresholds.py`) y `get_campaign_rules_for_org(organization)` (`app/tenant_rules/campaign_defaults.py`, nueva función) superponen solo las claves presentes en el override sobre los defaults globales (`dataclasses.replace`/merge de dict), ignorando claves desconocidas. Una org sin overrides se comporta exactamente igual que antes.
- **Consumidores actualizados** para resolver las reglas de la org en vez de leer la constante global: `CampaignSuggester.suggest`, `AIPlanner._create_action_from_insight` (placements y `CREATE_CAMPAIGN`), y las 3 tareas de la Saga que escriben a Meta (`task_create_campaign`, `task_create_adset`, `task_create_ad` en `creation_tasks.py`). `Capa1Rules`/`Capa2Statistics` no necesitaron cambios — ya pasaban `getattr(ad_account, "organization", None)` a `get_thresholds_for_org` desde antes (el extension point ya estaba cableado, solo la implementación era un no-op).
- Ver detalle de ejecución en [22-plan-remediacion-deuda §Sprint 3](22-plan-remediacion-deuda.md).

### ✅ M-3 · Modelos con uso marginal — RESUELTO
- **Explicación (histórica)**: `Campaign` + `CampaignMetricsHistory` tenían poco uso activo frente al modelo de snapshots; `create_temp_token`/`consume_temp_token` (dict en memoria) no tenían consumidores en los routers; `services/audit_log_service.py` + el modelo `ActionLog` (descubiertos al resolver M-1) tampoco tenían ningún llamador vivo.
- **Resolución**: confirmado con grep exhaustivo que ninguno de los cuatro tenía un consumidor real fuera de sí mismo. Se eliminaron `models/campaign.py`, `models/campaign_metrics_history.py`, `models/action_log.py`, `services/audit_log_service.py`, y las funciones `create_temp_token`/`consume_temp_token`/el dict `temp_tokens` de `app/utils/security.py`. Migración `b7e2f4a8c3d5` (drop de `campaign_metrics_history`, `campaigns`, `action_logs`, en ese orden por la FK; downgrade fiel, probado ida y vuelta contra Postgres real). Cobertura del backend subió a 86.74%.

### ✅ M-4 · Tipos frontend mantenidos a mano — RESUELTO
- **Explicación (histórica)**: `frontend/lib/types.ts` era un espejo manual de los schemas del backend. Un cambio de contrato podía desincronizarse en silencio (ej. `AIAction.reject_reason`, que sí llegó a pasar — ver UX-2 abajo).
- **Resolución**: se agregó `response_model` Pydantic a **todos** los endpoints que devolvían un dict plano sin schema (nuevo `app/schemas/response_schemas.py`: `BriefDataResponse`, `KpisSummaryResponse`, `SpendHistoryResponse`, `AiUsageResponse`, `ApiCallHistoryEntry`, `SuggestionResponse`, `LearningLogResponse`, `AiModelsResponse`, `BusinessPoliciesResponse`, `MessageResponse` y afines), de modo que `GET /openapi.json` ahora describe el 100% del API real. `frontend/lib/generated-api-types.ts` se genera de ahí con `npm run generate-types` (`openapi-typescript`); `frontend/lib/types.ts` quedó como una capa fina de alias (`export type X = components["schemas"]["XResponse"]`) sobre ese archivo generado, preservando los nombres cortos que ya usaban `lib/api.ts` y las páginas. Verificado con `tsc --noEmit`, `npm test` y `npm run build` limpios contra el schema real.
- Efecto colateral positivo: los tipos generados son más **estrictos** que los manuales (distinguen `Dict[str, Any]` como `{[key: string]: unknown}` en vez de `any`), lo que expuso 5 puntos donde el frontend accedía a JSON libre (`execution_result`, `payload.result`, `targeting`) sin narrowing — corregidos con un cast local puntual en cada uno.
- Ver también UX-2 (resuelto en el mismo cambio, sección "Producto / IA" más abajo).

## 3. Escalabilidad y rendimiento

### ✅ P-1 · Sincronización síncrona dentro de la tarea — RESUELTO
- **Explicación (histórica)**: `run_sync` hacía decenas de llamadas HTTP a Meta secuenciales por cuenta (`~12-15` en real). Con muchas cuentas, el worker `sync` (concurrency 2) podía volverse cuello de botella pese al escalonado.
- **Resolución**: se identificaron los pares/grupos de lecturas genuinamente independientes dentro de `_sync_real_data` (`app/services/meta_ads/sync.py`) — ninguna hace una escritura a `self.db` antes de que termine el fetch, así que pedirlas con `asyncio.gather` en vez de una tras otra no introduce ninguna carrera sobre la sesión de SQLAlchemy (que sí sería insegura si se paralelizaran escrituras) — y se paralelizaron: campañas + insights de campaña, estructura + insights de adsets, estructura + insights de ads, imágenes + audiencias del inventario, y las **5 dimensiones de breakdown** (antes 5 llamadas una tras otra, el mayor cuello de botella individual). `MetaAPIRateLimiter.call_api` ya era seguro para invocación concurrente (cada llamada abre su propio `httpx.AsyncClient` y su propia sesión de log — sin estado mutuo compartido salvo un contador cosmético sin lectores). Reduce de ~16 a ~7 round-trips secuenciales por cuenta (más N llamadas por página, inherentemente secuenciales por depender del `page_id` anterior).
- **No incluido**: paralelizar las escrituras a BD entre sub-syncs (adsets/ads/inventario/páginas/breakdowns como fases completas) — requeriría separar fetch/write en cada uno y usar sesiones de BD independientes por coroutine, un refactor bastante más invasivo para un beneficio marginal (las escrituras son rápidas; el cuello de botella real es la latencia de red de Meta, ya atacada).
- Verificado con la suite existente (`tests/test_audit_fixes.py::test_real_sync_parses_meta_entities` y afines, que ejercen `_sync_real_data` completo contra un limiter fake) — sin cambios de comportamiento, solo de orden/paralelismo de las llamadas HTTP.

### ✅ P-2 · Estado en memoria del proceso — RESUELTO (por verificación, sin cambio de código)
- **Explicación (histórica)**: el `ConnectionManager` de WebSocket y `temp_tokens` vivían en memoria del proceso FastAPI. Con múltiples réplicas del backend, un cliente conectado a la réplica A no recibía un broadcast originado en B salvo por el pubsub (que sí resolvía WS, pero no el token temporal).
- **Resolución**: `temp_tokens`/`create_temp_token`/`consume_temp_token` ya no existen — se eliminaron por completo en [M-3](15-deuda-tecnica.md) (Sprint 2), que confirmó con grep exhaustivo que no tenían ningún consumidor real. Eso deja solo la mitad del `ConnectionManager` de WS, que **ya estaba resuelta desde antes** (el propio hallazgo original lo decía: "el pubsub... sí resuelve WS"): `manager.active_connections` (`app/api/v1/ws.py`) es efectivamente estado local por réplica, pero cada réplica corre su propio `redis_listener()` suscrito al mismo canal (`ws_notifications`) que `NotificationService.notify_job_status` publica — un evento originado en cualquier réplica llega a **todas**, y cada una hace `broadcast_to_tenant` solo a sus conexiones locales. Es el patrón estándar de fan-out para WS horizontalmente escalado, no un parche pendiente. Confirmado con grep: `active_connections`/`ConnectionManager` no se referencian fuera de `ws.py` (sin lector cross-réplica que necesite el estado). El frontend ya reconecta automáticamente cada 3s (`WebSocketProvider`) ante cualquier caída de conexión (reinicio de réplica, rebalanceo), así que no hay pérdida de eventos observable por el usuario.

## 4. Operación

### ✅ O-1 · Sin CI/CD versionado — RESUELTO
- **Explicación (histórica)**: no había pipeline (`.github/workflows`, etc.). Los tests existían pero no se corrían automáticamente; el deploy era manual (`build-push.sh` + `pull`).
- **Resolución**: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — job `backend` (instala dependencias de sistema de WeasyPrint, corre `alembic upgrade head` contra un servicio Postgres 16 efímero, y `pytest` con cobertura) + job `frontend` (`tsc --noEmit`, `npm test`, `npm run build`). Corre en push/PR a `main`.
- **Hallazgo real durante la implementación**: al ejercer `alembic upgrade head` contra Postgres real por primera vez (los tests de pytest usan SQLite vía `Base.metadata.create_all`, nunca habían corrido la cadena de migraciones), la migración `a70ab36472b6_add_payload_json_to_sync_jobs.py` fallaba con `NameError: name 'Text' is not defined` (usaba `Text()` sin importar, en vez de `sa.Text()`) — un bug real, invisible hasta ahora porque nada lo ejercía. Corregido en el mismo cambio. Confirma exactamente el riesgo que este hallazgo advertía.
- CD (despliegue automático) queda fuera de este alcance — el pipeline valida, no despliega.

### 🟡 O-2 · Migraciones como paso manual del deploy
- **Explicación**: `alembic upgrade head` es manual tras cada `pull`.
- **Impacto**: riesgo de olvidar migrar tras un deploy.
- **Propuesta**: entrypoint/init-container que corra las migraciones antes de arrancar el API (con cuidado en despliegues multi-réplica). Planificado para el Sprint 5 de [22-plan-remediacion-deuda](22-plan-remediacion-deuda.md).

### ✅ O-3 · Cobertura de tests no medida — RESUELTO
- **Explicación (histórica)**: no había umbral de cobertura ni reporte.
- **Resolución**: `pytest-cov` añadido a `requirements.txt`; CI corre `pytest -q --cov=app --cov-report=term-missing --cov-fail-under=80` (cobertura real medida: ~84%). Un PR que baje la cobertura del backend por debajo de 80% falla en CI.

## 5. Producto / IA

### 🟡 IA-1 · Ejecución real de modificaciones aún no ejercida a gran escala
- **Explicación**: `MetaCampaignModifier` y la Saga tienen caminos reales bien construidos (backup, rollback, reglas de seguridad), pero el write-back está apagado por defecto y validado solo con cuenta piloto.
- **Impacto**: riesgo residual al encender `ENABLE_META_WRITEBACK` en todo el parque.
- **Propuesta**: seguir el checklist de `F-2` (allowlist), y añadir tests de integración contra un entorno de sandbox de Meta.

### 🟡 UX-1 · El "Estado" de una campaña se muestra de dos formas distintas
- **Explicación**: la lista `/campaigns` usa `metaStatusBadge` (Activa/Pausada/Archivada/Eliminada, español) y separa una columna "Rendimiento"; pero la tabla "Resumen de Campañas Activas" del dashboard sigue pintando el `status` crudo en inglés (`ACTIVE`→verde, resto→ámbar). Ver [21-producto-datos-visualizados](21-producto-datos-visualizados.md) §2.4.
- **Impacto**: inconsistencia visual; el mismo concepto se lee distinto en dos pantallas, y el dashboard no distingue estado real de rendimiento.
- **Propuesta**: extraer un componente `StatusBadge` único y usarlo en ambas vistas.

### ✅ UX-2 · El motivo de rechazo de una acción no se muestra — RESUELTO
- **Explicación (histórica)**: al rechazar una `AIAction` se persistía `reject_reason` (y `rejected_at`), pero `AIActionResponse` no los exponía, así que el historial de acciones nunca mostraba cuándo ni por qué se rechazó algo.
- **Resolución**: ambos campos agregados a `AIActionResponse` (`app/schemas/schemas.py`). Sin cambios de frontend — `/actions` ya tenía el JSX listo (`Razón: {act.reject_reason}`), solo esperaba el dato. Test: `test_action_reject_exposes_reason_in_response` en `backend/tests/test_audit_fixes.py`.

### 🟡 UX-3 · `findings`/`recommendations` no llegan al dashboard
- **Explicación**: el contenido más accionable (frases deterministas de "lo más importante" y "qué hacer") solo se renderiza en `/reporte`, no en el home donde el cliente entra primero.
- **Impacto**: el mejor insight del sistema queda a un clic de distancia de la pantalla principal.
- **Propuesta**: exponer 1–2 findings/recommendations en el dashboard (el dato ya viaja en `brief-data`).

## 6. Resumen priorizado

| ID | Gravedad | Área | Esfuerzo estimado | Estado |
|----|----------|------|-------------------|--------|
| S-1 | 🔴 | Autorización RBAC | Medio | ✅ Resuelto |
| S-2 | 🟠 | Tokens en localStorage | Medio | ✅ Resuelto |
| S-3 | 🟠 | Blocklist JWT fail-open | Bajo | ✅ Resuelto |
| M-1 | 🟠 | Consolidar clientes Meta | Medio | ✅ Resuelto |
| M-2 | 🟠 | Reglas por tenant | Alto | ✅ Resuelto |
| P-1 | 🟠 | Paralelizar sync | Medio | ✅ Resuelto |
| O-1 | 🟠 | CI/CD | Bajo-Medio | ✅ Resuelto |
| S-4, S-5 | 🟡 | Endurecimiento | Bajo | ✅ Resuelto |
| M-3 | 🟡 | Modelos marginales | Bajo | ✅ Resuelto |
| M-4 | 🟡 | Tipos generados desde OpenAPI | Bajo | ✅ Resuelto |
| P-2 | 🟡 | Estado en memoria (WS) | Bajo | ✅ Resuelto (por verificación) |
| O-2, IA-1 | 🟡 | Limpieza/operación | Bajo-Medio | Pendiente |
| O-3 | 🟡 | Cobertura de tests | Bajo | ✅ Resuelto |
| UX-2 | 🟡 | reject_reason expuesto | Trivial | ✅ Resuelto |
| UX-1, UX-3 | 🟡 | Experiencia de producto | Bajo | Pendiente |

Ver el detalle de ejecución y el resto de sprints en [22-plan-remediacion-deuda](22-plan-remediacion-deuda.md).
