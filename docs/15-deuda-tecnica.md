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

### 🟡 S-4 · `X-Forwarded-For` sin lista de proxies confiables
- **Explicación**: `get_client_ip` toma el primer valor de `X-Forwarded-For`; es spoofeable si algo distinto de Nginx llega al backend.
- **Impacto**: evasión del rate limit por IP (solo relevante si el backend queda expuesto sin el proxy).
- **Propuesta**: confiar solo en el proxy conocido; documentar que el backend no debe exponerse directo.

### 🟡 S-5 · Prompt injection desde datos de Meta
- **Explicación**: nombres de campaña y copy reales se inyectan en prompts del LLM. El system prompt mitiga (no inventar cifras) pero no hay sanitización anti-inyección.
- **Impacto**: un nombre de campaña malicioso podría intentar manipular la narrativa. Bajo, porque la salida es texto informativo, no acciones.
- **Propuesta**: delimitar/escapar el contenido no confiable en el prompt; mantener el determinismo de las decisiones (ya se hace).

## 2. Arquitectura y mantenibilidad

### 🟠 M-1 · Dos clientes de Meta y dos rate limiters coexistiendo
- **Explicación**: `MetaAPIRateLimiter` (usado en todo el flujo real) y `MetaApiClient`+`services/rate_limiter.py`+`meta_credential_service.py` (alternativo, apenas usado). También hay `TenantApiQuota` que alimenta un limiter distinto.
- **Impacto**: confusión sobre cuál es el camino canónico; código potencialmente muerto; dos estrategias de rate limit.
- **Propuesta**: consolidar en un único cliente/limiter o documentar explícitamente el rol de cada uno y eliminar el no usado.

### 🟠 M-2 · Reglas de negocio globales presentadas como "por tenant"
- **Explicación**: `TENANT_CAMPAIGN_RULES` (geo Lima, objetivo, placements) y `AIThresholds` son **globales**, con TODOs para moverlas a tabla por tenant. El país/moneda (`PEN`/Perú) está hardcodeado en varios lugares (engine, targeting, modifier con key `2771`).
- **Impacto**: onboarding de un cliente fuera de Lima/Perú requiere cambios de código.
- **Propuesta**: materializar `get_thresholds_for_org` y `TENANT_CAMPAIGN_RULES` contra una tabla de reglas por organización/vertical.

### 🟡 M-3 · Modelos con uso marginal
- **Explicación**: `Campaign` + `CampaignMetricsHistory` parecen tener poco uso activo frente al modelo de snapshots; `create_temp_token`/`consume_temp_token` (dict en memoria) no tienen consumidores en los routers.
- **Impacto**: código/esquema que puede confundir a un nuevo dev.
- **Propuesta**: confirmar uso y eliminar/documentar; el token temporal en memoria además no funciona con múltiples réplicas.

### 🟡 M-4 · Tipos frontend mantenidos a mano
- **Explicación**: `frontend/lib/types.ts` es un espejo manual de los schemas del backend (ya documentado en el propio archivo). Un cambio de contrato puede desincronizarse silenciosamente (ej. `AIAction.reject_reason`, ya anotado).
- **Impacto**: bugs de tipado sutiles entre front y back.
- **Propuesta**: generar tipos desde el OpenAPI del backend (`openapi-typescript`).

## 3. Escalabilidad y rendimiento

### 🟠 P-1 · Sincronización síncrona dentro de la tarea
- **Explicación**: `run_sync` hace decenas de llamadas HTTP a Meta secuenciales por cuenta (`~12-15` en real). Con muchas cuentas, el worker `sync` (concurrency 2) puede volverse cuello de botella pese al escalonado.
- **Impacto**: latencia del pipeline diario crece linealmente con el número de cuentas.
- **Propuesta**: paralelizar lecturas independientes por cuenta, o particionar el fan-out; monitorear duración de sync.

### 🟡 P-2 · Estado en memoria del proceso
- **Explicación**: el `ConnectionManager` de WebSocket y `temp_tokens` viven en memoria del proceso FastAPI. Con múltiples réplicas del backend, un cliente conectado a la réplica A no recibe un broadcast originado en B salvo por el pubsub (que sí resuelve WS, pero no el token temporal).
- **Impacto**: limita el escalado horizontal del backend.
- **Propuesta**: el WS ya usa Redis pubsub (bien); eliminar el token en memoria o moverlo a Redis.

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

### 🟡 UX-2 · El motivo de rechazo de una acción no se muestra
- **Explicación**: al rechazar una `AIAction` se persiste `reject_reason`, pero `AIActionResponse` no lo expone (ya anotado en `types.ts` y M-4), así que el historial de acciones nunca muestra por qué se rechazó.
- **Impacto**: se pierde trazabilidad de las decisiones humanas; loop de auditoría/aprendizaje incompleto.
- **Propuesta**: añadir `reject_reason` a `AIActionResponse` y renderizarlo en `/actions` (historial).

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
| M-1 | 🟠 | Consolidar clientes Meta | Medio | Pendiente |
| M-2 | 🟠 | Reglas por tenant | Alto | Pendiente |
| P-1 | 🟠 | Paralelizar sync | Medio | Pendiente |
| O-1 | 🟠 | CI/CD | Bajo-Medio | ✅ Resuelto |
| S-4, S-5 | 🟡 | Endurecimiento | Bajo | Pendiente |
| M-3, M-4, P-2, O-2, IA-1 | 🟡 | Limpieza/operación | Bajo-Medio | Pendiente |
| O-3 | 🟡 | Cobertura de tests | Bajo | ✅ Resuelto |
| UX-1, UX-2, UX-3 | 🟡 | Experiencia de producto | Bajo | Pendiente |

Ver el detalle de ejecución y el resto de sprints en [22-plan-remediacion-deuda](22-plan-remediacion-deuda.md).
