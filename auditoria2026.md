# Auditoría Técnica LejoVista — 2026

> Documento de auditoría de nivel consultoría externa. Cada hallazgo está firmado por la disciplina que mejor lo detecta.
> Severidades: 🔴 Crítico (datos/seguridad/caída en prod) · 🟡 Importante (deuda, performance, UX que duele) · 🟢 Mejora.
> Esfuerzo: **S** (<1 día) · **M** (1–3 días) · **L** (>3 días / cross-team).
>
> Alcance revisado: repo completo en `/Users/luisr/Proyectos/Proyecto lejovista` a fecha 2026-07-03. Se leyó todo el backend (`api/v1`, `models`, `services`, `workers`, `schemas`, `config`, `database`), el frontend (`app/(dashboard)`, `lib`, `middleware`), `docker-compose*.yml`, migraciones Alembic, los prompts `paso0/paso1` y el `informe-rendimiento-jun2026.html`.

---

## 1. Resumen Ejecutivo

LejoVista es un piloto **impresionante en superficie y frágil en los cimientos**. El flujo end-to-end (conectar cuenta → sync → 3 capas de IA → narrativa → dashboard) funciona, pero **hoy funciona casi enteramente en modo mock**, y el `.env` real tiene `MOCK_SERVICES=false` con una `GEMINI_API_KEY` viva — es decir, la configuración que se está usando activa rutas de código reales que están a medio cablear.

Hay **cinco hallazgos 🔴 de seguridad/datos que deben resolverse antes de cualquier feature nueva**:

1. **Claves de cifrado y firma JWT committeadas** en `.env.example` (`SECRET_KEY`, `ENCRYPTION_KEY` de Fernet). Cualquiera con acceso al repo puede **desencriptar todos los tokens de Meta y falsificar sesiones de cualquier tenant**.
2. **El router `/api/v1/sync/` no tiene autenticación** y su verificación de pertenencia al tenant está literalmente comentada (`pass`). `GET /sync/{job_id}/status` es un IDOR que devuelve el brief de cualquier job por ID.
3. **El WebSocket `/ws/notifications/{tenant_id}` no valida nada**: cualquiera se suscribe a las notificaciones de cualquier tenant.
4. **Tareas de Celery no registradas en el worker** (`sync_tasks`, `creation_tasks` no están en `include`): la creación de campañas y el sync alternativo **fallan en runtime** al ejecutarse.
5. **Alucinación numérica no controlada + narrativa mock con cifras inventadas**: el dashboard muestra por defecto una narrativa con CPLs fabricados (`S/.1.45`, `S/.12.00`) que no salen de los datos del cliente.

Sobre **compliance con Meta**: tanto los prompts manuales como el `rate_limiter.py` del backend **imitan un navegador Chrome y aleatorizan delays "para evitar detección de automatización"**. Esto es una **violación directa de las Meta Platform Terms**, independientemente de que el token sea legítimo. Es el riesgo existencial del negocio (ban del Business Manager) y hay que desmontarlo, no formalizarlo.

Sobre **producto**: el `AIEngine` real solo persiste métricas a nivel campaña + un breakdown de placements y de edad. El informe de referencia hecho a mano tiene **8 secciones** (adset, creativo, dispositivo, género, geografía, audiencias, inventario de creativos, parámetros óptimos) de las cuales **el 70% no tiene siquiera el dato capturado hoy**. El `GET /reports/brief-data` está muy lejos de reproducir ese informe.

**Recomendación de secuencia**: la prioridad del equipo (write-back → Celery Beat → PDF) está **invertida**. Primero seguridad y consolidación de arquitectura, luego captura de datos, y recién después write-back (que además ya está parcialmente implementado y cableado a presupuestos hardcodeados — es peligroso activarlo tal cual).

---

## 2. Matriz de Hallazgos por Eje

### 2.1 Seguridad / AppSec

**S-1 🔴 — Claves criptográficas committeadas en `.env.example`** · *(AppSec / Security Engineer)*
**Evidencia:** `.env.example:37-38` contiene `SECRET_KEY=947c61...` y `ENCRYPTION_KEY=sS31u3Gj6qK0...` (clave Fernet válida). `config.py:21-22` declara ambas sin default (obligatorias); `.env` real reusa exactamente los mismos valores. `MetaCredential.set/get_access_token` (`meta_credential.py:24-32`) cifra con esa clave.
**Por qué importa:** la `ENCRYPTION_KEY` es la única barrera entre la DB y los access tokens de Meta en claro. Al estar en el repo, cualquier persona con acceso al historial de git puede desencriptar **todos** los tokens de todos los tenants y, con `SECRET_KEY`, forjar un JWT válido para cualquier `user_id`. Es una fuga total de multi-tenancy.
**Recomendación:** rotar ambas claves **ya** (invalida sesiones y obliga a re-cifrar tokens). Poner solo placeholders en `.env.example` (`SECRET_KEY=changeme-generate-with-openssl`). Cargar secretos reales desde un gestor (Docker secrets / SOPS / Vault), nunca desde archivos versionados. Verificar `git log -p .env.example` y purgar del historial si hubo tokens reales.
**Esfuerzo:** S (rotación) + M (gestor de secretos).

**S-2 🔴 — `/api/v1/sync/` sin auth y con chequeo de tenant comentado** · *(AppSec + Backend)*
**Evidencia:** `api/v1/sync.py:13-53`. La firma no incluye `Depends(get_current_user)`. `tenant_id: str = "mock-tenant-id"` viene de query param. El bloque de validación de pertenencia (`sync.py:26-31`) tiene el `raise` comentado y un `pass`. `GET /sync/{job_id}/status` (`sync.py:56-79`) no filtra por tenant y devuelve `job.payload_json` (el Master Brief) de **cualquier** job.
**Por qué importa:** endpoint no autenticado que (a) encola trabajos de Celery a voluntad — vector de DoS y de baneo de Meta por costo/llamadas, y (b) filtra briefs completos de otros clientes por fuerza bruta de UUID. Es el agujero más grave de aislamiento.
**Recomendación:** añadir `current_user = Depends(get_current_user)`, derivar `tenant_id` del usuario, y validar que `ad_account_id` pertenezca a `current_user.organization_id` **antes** de encolar. En `/status`, verificar `job.organization_id == current_user.organization_id`. Si este router es legacy/duplicado de `campaigns.sync-trigger`, **eliminarlo**.
**Esfuerzo:** S.

**S-3 🔴 — WebSocket sin autenticación ni validación de tenant** · *(AppSec)*
**Evidencia:** `api/v1/ws.py:80-94`. `tenant_id` viene del path sin verificación. El propio comentario lo admite ("en un sistema real, tenant_id se extraería/validaría de un token"). El frontend se conecta con el slug tomado del hostname (`layout.tsx:153`).
**Por qué importa:** cualquiera que adivine/conozca un slug (son públicos: subdominios) recibe en tiempo real los eventos de sync y creación de campañas de ese tenant. Fuga de metadatos operativos cross-tenant.
**Recomendación:** exigir el JWT como query param (`?token=`), validarlo en el handshake, resolver el tenant desde el token y rechazar si no coincide con el slug. Cerrar conexión con code 4401 si falla.
**Esfuerzo:** S/M.

**S-4 🔴 — Cumplimiento de Meta ToS: anti-detección disfrazando la Marketing API** · *(AppSec / Compliance)*
**Evidencia:** `services/meta_ads/rate_limiter.py:19-26` (User-Agent de Chrome falso) y `:45-66` ("Pausa preventiva anti-detección", delays aleatorios). Los prompts `paso1DescargarInfoApi.md` (reglas 1, 6: timing 12-22s, headers de navegador, "las pausas idénticas son un patrón de bot") y `paso0crearCampañas.md` (mismo patrón para escrituras). La UI lo celebra: `actions/page.tsx:240` muestra *"Ejecutando llamadas API con pausas anti-detección"*.
**Por qué importa:** las Meta Platform Terms y la Marketing API Terms prohíben tergiversar el cliente, evadir límites de tasa y simular tráfico humano. Un token legítimo **no** legitima disfrazar tráfico automatizado como navegador. El riesgo no es un rate-limit puntual: es la **inhabilitación del Business Manager y de la app** — pérdida total del canal para todos los clientes de la agencia. Además es innecesario: la Marketing API está diseñada para consumo programático con su propio esquema de límites (`x-business-use-case-usage`, que el código ya lee).
**Recomendación:** eliminar el User-Agent falso y usar uno honesto (`LejoVista/1.0` — ya existe en `meta_api_client.py:17`). Sustituir los "delays anti-detección" por backoff basado exclusivamente en `x-business-use-case-usage` y `estimated_time_to_regain_access`. Registrar la app en el App Review de Meta con los permisos correctos. **No** formalizar el workflow manual como feature con estas tácticas; formalizarlo **sin** ellas (ver S-5).
**Esfuerzo:** M.

**S-5 🟡 — Tokens pegados en texto plano en prompts de chat** · *(AppSec / Compliance)*
**Evidencia:** `paso0crearCampañas.md` ("Token de acceso: [PEGA TOKEN AQUI]") y `paso1DescargarInfoApi.md` (regla 7). El propio prompt advierte "nunca loguear ni guardar tokens en texto plano" mientras exige pegarlo en el chat.
**Por qué importa:** el token de Meta pegado en un historial de chat queda cacheado/indexado por el proveedor del asistente, potencialmente auditable y fuera del control de la agencia — contradicción directa con la advertencia del propio prompt. La app, en cambio, cifra con Fernet en `meta_credentials.access_token_encrypted` (bien), pero mientras exista el workflow paralelo el token vive en dos mundos con posturas de seguridad opuestas.
**Recomendación:** deprecar el flujo de pegar tokens en prompts. Toda operación (lectura y escritura) debe pasar por la app, que ya tiene almacenamiento cifrado. Si se necesita el flujo manual como puente, que la app exponga un endpoint autenticado que ejecute las mismas llamadas server-side.
**Esfuerzo:** M/L (implica mover creación de campañas a la app de forma robusta).

**S-6 🟡 — Expiración de tokens de Meta no gestionada** · *(AppSec / Backend)*
**Evidencia:** `meta_credential.py:15` tiene `token_expires_at` pero **nada lo setea**. `meta_credential_service.py:10-29` (`get_valid_token`) solo filtra `status == "ACTIVE"`, nunca compara expiración. No hay refresh de long-lived tokens ni intercambio short→long-lived.
**Por qué importa:** los tokens de usuario de Meta expiran (~2h short-lived, ~60 días long-lived). Sin manejo, los syncs empezarán a fallar silenciosamente a los 60 días de conectar cada cuenta, sin alerta ni re-prompt al cliente. Para una agencia con muchas cuentas es un goteo de fallos invisibles.
**Recomendación:** al conectar, intercambiar por long-lived token (`GET /oauth/access_token?grant_type=fb_exchange_token`), guardar `token_expires_at`, y en `get_valid_token` marcar `EXPIRED` + generar un `AIInsight`/notificación cuando falten <7 días. 
**Esfuerzo:** M.

**S-7 🟡 — `print()` de DEBUG con datos de tenant en cada request autenticado** · *(AppSec / SRE)*
**Evidencia:** `utils/security.py:99` — `print(f"DEBUG: tenant_slug=..., user_org_ids=..., user.organizations=...")` corre en `get_current_user` en **cada** petición cuando hay header de tenant.
**Por qué importa:** filtra IDs de organización y relaciones de usuario a stdout/logs en producción, y añade ruido/costo. Es un rastro clásico de debug olvidado.
**Recomendación:** eliminarlo. Adoptar logging estructurado con niveles (ver I-4).
**Esfuerzo:** S.

**S-8 🟡 — Multi-tenancy real depende de `organizations[0]`; header `X-Tenant-Slug` es cosmético** · *(AppSec / Arquitecto)*
**Evidencia:** `user.py:19-23` — `organization_id` devuelve siempre `self.organizations[0].organization_id`. Todo el aislamiento de datos correcto (los endpoints filtran por `current_user.organization_id`, p.ej. `insights.py:24`, `actions.py:24`) cuelga de ese "primer" elemento. El chequeo de `X-Tenant-Slug` (`security.py:91-104`) solo corre `if tenant_slug` y solo verifica pertenencia, no "tenant activo".
**Por qué importa:** (1) un usuario que pertenezca a 2+ organizaciones **nunca** podrá ver la segunda — bug funcional latente para agencias con staff compartido. (2) La narrativa del `status.md` ("aislamiento por header X-Tenant-Slug") es engañosa: el header no es el mecanismo de seguridad (bien, porque sería falsificable), pero la doc induce a confiar en él.
**Recomendación:** modelar explícitamente el "tenant activo" (claim en el JWT al hacer login, seleccionable), y que `get_current_user` derive el `organization_id` de ese claim validado contra `organizations`. Documentar que el slug es solo enrutamiento.
**Esfuerzo:** M.

**S-9 🟢 — JWT sin refresh ni revocación; `temp_tokens` en memoria** · *(AppSec)*
**Evidencia:** `security.py:28-36` (HS256, exp 24h fijo), `:19-55` (`temp_tokens` dict en proceso). Token guardado en `localStorage` (`api.ts:10`).
**Por qué importa:** no hay logout server-side ni revocación ante compromiso; `localStorage` es accesible a XSS. `temp_tokens` no sobrevive multi-worker ni reinicios.
**Recomendación:** access token corto (15-30 min) + refresh token httpOnly; blocklist de jti en Redis para revocación. Mover `temp_tokens` a Redis si se usa.
**Esfuerzo:** M.

**S-10 🟢 — CORS y `create_campaign` con cliente Meta mockeado a `localhost`** · *(AppSec / Backend)*
**Evidencia:** `campaigns.py:105-112` — `get_meta_client` crea `redis.Redis.from_url("redis://localhost:6379/0")` (sin password, host equivocado en Docker) y `MetaApiClient("mock-tenant", ...)` con tenant hardcodeado. `main.py:48-53` CORS con regex amplio sobre `localhost` y `allow_credentials=True`.
**Por qué importa:** el endpoint de creación de campañas está cableado a infraestructura de dev y a un tenant fijo; en prod fallaría o cruzaría contextos. El CORS es tolerable pero conviene endurecerlo.
**Recomendación:** inyectar el cliente Meta vía dependencia real con el tenant del usuario y el Redis de settings. Restringir CORS a orígenes de prod.
**Esfuerzo:** S/M.

---

### 2.2 Arquitectura y Calidad de Código (Backend)

**A-1 🔴 — Tareas de Celery no registradas en el worker → fallo en runtime** · *(Arquitecto / Backend)*
**Evidencia:** `workers/celery_app.py:8` — `include=["app.workers.tasks"]` **solo**. Las tareas de `workers/sync_tasks.py` (`task_sync_tenant_data`, `fetch_meta_data_task`, `task_generate_master_brief`) y `workers/creation_tasks.py` (`task_upload_image`…`task_create_ad`, `task_handle_chain_error`) no se importan en el worker. `campaigns.py:23-30` y `sync.py:8` las invocan con `.delay()`/`chain()`.
**Por qué importa:** los routers registran las tareas en el proceso FastAPI, pero el proceso `celery-worker` solo importa `include`. Al ejecutarse el chain de creación o el sync alternativo, el worker recibe una tarea que **no conoce** → `NotRegistered`, la saga muere y (por A-2) sin retry ni alerta. La creación de campañas está rota de raíz.
**Recomendación:** añadir `"app.workers.sync_tasks"` y `"app.workers.creation_tasks"` al `include`, o mejor usar `celery_app.autodiscover_tasks(["app.workers"])`. Añadir un test de smoke que verifique `celery_app.tasks` contiene todas las esperadas.
**Esfuerzo:** S.

**A-2 🔴 — Reintentos de Celery declarados pero nunca configurados** · *(Backend / SRE)*
**Evidencia:** `tasks.py:14` y `creation_tasks.py:14` usan `@celery_app.task(bind=True, max_retries=3)` pero **ningún** `self.retry()`, `autoretry_for` ni `retry_backoff`. `sync_tasks.py:15` dice en comentario "gracias a bind=True y autoretry_for (configurado abajo)" — **no está configurado**. En `sync.py`→`MetaAdsSyncService.run_sync` (`meta_ads/sync.py:45-52`) un error de Meta se captura, marca el `SyncJob` como `FAILED` y **retorna normal** (falla silenciosa: sin retry, sin dead-letter, sin alerta al equipo).
**Por qué importa:** un 429 o token expirado a mitad de sync deja el job en FAILED sin reintento ni notificación operativa. Con Celery Beat diario (roadmap) esto se vuelve un fallo masivo invisible.
**Recomendación:** `@task(autoretry_for=(TransientMetaError,), retry_backoff=True, retry_backoff_max=600, max_retries=5, acks_late=True)`. Distinguir errores transitorios (429/5xx) de permanentes (400/403). Dead-letter queue + alerta (Sentry/Slack) en fallo definitivo.
**Esfuerzo:** M.

**A-3 🟡 — Dos stacks paralelos y divergentes para hablar con Meta** · *(Arquitecto)*
**Evidencia:** Stack A (usado por el flujo real): `services/meta_ads/{rate_limiter,sync,modifier}.py`. Stack B (usado por `sync.py` router y `campaigns.create`): `services/{meta_api_client,rate_limiter,meta_credential_service,meta_sync_service,meta_insights_service,master_brief_generator}.py` + `workers/sync_tasks.py`. Dos rate limiters distintos, dos formas de obtener el token (`ad_account.get_access_token()` vs `MetaCredentialService.get_valid_token(org)`), dos generadores de brief.
**Por qué importa:** duplicación conceptual que multiplica superficie de bugs y confunde qué ruta es la "de verdad". El Stack B está mayormente stubbeado (`meta_insights_service.save_query_results` no hace nada, `master_brief_generator.generate_brief` retorna estructura vacía) pero está cableado a endpoints públicos.
**Recomendación:** elegir un solo cliente Meta (el `meta_api_client.py` es el más limpio: rate-limit por header, auditoría, User-Agent honesto). Migrar `sync.py` de `meta_ads/` a usarlo o borrar el Stack B. Consolidar el modelo de credenciales (hoy conviven `AdAccount.meta_credential_id` y consultas directas a `MetaCredential` por org).
**Esfuerzo:** L.

**A-4 🟡 — Lógica de negocio filtrada en controladores** · *(Arquitecto / Backend)*
**Evidencia:** `reports.py:38-54` instancia el `AIEngine`, llama a `_get_recent_campaigns_summary` (método "privado"), invoca el LLM y **hace `commit` dentro de un GET**. `campaigns.py:114-161` arma la saga `chain(...)` inline con la orquestación completa.
**Por qué importa:** un GET con efectos de escritura (genera y persiste narrativa) rompe idempotencia HTTP, puede disparar costo de LLM en una recarga de página y bloquea la request hasta 45s. La orquestación en el controlador impide testear/reusar.
**Recomendación:** mover generación de narrativa a la capa de servicio y a Celery (nunca en el GET); el GET solo lee `cached_narrative`. Extraer la construcción de la saga a `CampaignCreationService`.
**Esfuerzo:** M.

**A-5 🟡 — `run_async` con `get_event_loop().run_until_complete` es frágil en Celery** · *(Backend)*
**Evidencia:** `tasks.py:16-23` y `sync_tasks.py:10-12`. Reutilizan el event loop del hilo.
**Por qué importa:** con el pool prefork de Celery y loops reusados es fácil caer en `RuntimeError: Event loop is closed` o loops ya corriendo tras la primera tarea. Es una fuente típica de flakiness intermitente difícil de diagnosticar.
**Recomendación:** usar `asyncio.run(coro)` por tarea (loop nuevo y cerrado limpio), o adoptar `celery-pool-asyncio`/`asgiref.async_to_sync`. Unificar en un solo helper.
**Esfuerzo:** S.

**A-6 🟡 — Sin cobertura de las rutas de fallo; el "happy path" mockeado es lo único probado** · *(Backend / QA)*
**Evidencia:** existen `backend/tests/test_{api_v1,services,workers}.py` (~630 líneas) y `frontend/__tests__/*` — bien que existan. Pero mockean Celery y corren contra datos mock; no hay tests de: token expirado a mitad de sync, 429 de Meta, idempotencia de sync real, ni de que las tareas estén registradas (A-1 habría sido detectado por un test).
**Por qué importa:** el sistema "pasa la prueba piloto" precisamente porque el piloto es el happy path mock. Los 🔴 A-1 y B-2 no los cubre ningún test.
**Recomendación:** tests de integración con un doble de la Graph API (respondiendo 429/400/expired) y un test que asserte `set(expected) <= set(celery_app.tasks)`.
**Esfuerzo:** M.

**A-7 🟢 — `main.py` crea el esquema con `create_all`, ignorando Alembic** · *(Backend / DBA)* — ver D-5.

---

### 2.3 Base de Datos (DBA)

**D-1 🔴 — `campaign_snapshots` sin unicidad (ad_account_id, campaign_id, date) → duplicados en sync real** · *(DBA / Data Architect)*
**Evidencia:** `models/campaign_snapshot.py:33-35` solo define `Index("idx_snapshot_org_date", ...)`. El sync mock **sí** deduplica en código (`meta_ads/sync.py:117-125`), pero `_sync_real_data` (`sync.py:284-335`) inserta sin comprobar existencia. Contraste: `campaign_metrics_history.py:22-24` sí tiene `UniqueConstraint("campaign_id","date")` — pero esa tabla no es la que usa el flujo real.
**Por qué importa:** en producción, dos ejecuciones del sync para la misma fecha (reintento, doble click, Celery Beat + trigger manual) **duplican snapshots**, y como el `AIEngine` agrega sumando (`engine.py:127-130`, `rules.py:73-77`), el spend/leads/CPL de los diagnósticos se **duplica** → decisiones de gasto sobre datos inflados.
**Recomendación:** `UniqueConstraint("ad_account_id","campaign_id","date")` + `INSERT ... ON CONFLICT DO UPDATE` (upsert) en el sync. Migración Alembic dedicada.
**Esfuerzo:** S/M.

**D-2 🟡 — Falta índice compuesto `(ad_account_id, date)`, patrón de query dominante** · *(DBA)*
**Evidencia:** la query más frecuente (Capa 1, Capa 2, `_get_recent_campaigns_summary`) filtra `ad_account_id == X AND date >= Y` (`rules.py:50-53`, `statistics.py:25-28`, `engine.py:107-110`). Hay índices sueltos en `ad_account_id` y en `date`, y un compuesto en `(organization_id, date)`, pero **no** en `(ad_account_id, date)`.
**Por qué importa:** en la "tabla más pesada", que crece a snapshots × campañas × cuentas × día, el planner hará scan por `ad_account_id` y filtrará fechas en memoria. A escala (miles de filas por cuenta) la latencia del diagnóstico sube — y esa latencia es, para el UX designer, un spinner largo en `/diagnostics`.
**Recomendación:** `Index("idx_snapshot_acc_date", "ad_account_id", "date")`. Reevaluar si el compuesto `(organization_id, date)` aporta o es redundante.
**Esfuerzo:** S.

**D-3 🟡 — Sin estrategia de particionado/archivado para crecimiento indefinido** · *(DBA / Data Architect)*
**Evidencia:** `campaign_snapshots` acumula un snapshot diario por campaña por cuenta, para siempre. No hay retención ni particiones.
**Por qué importa:** el `AIEngine` solo mira 7-30 días, pero la tabla guarda historia infinita. A 1-2 años con decenas de cuentas activas son millones de filas calientes innecesarias, degradando índices y backups.
**Recomendación:** particionado nativo de Postgres por rango de `date` (mensual) con detach+archivo a cold storage de particiones >12-18 meses. Alternativamente rollups: mantener diario 90 días, semanal/mensual agregado más atrás.
**Esfuerzo:** M/L.

**D-4 🟡 — `cached_narrative` sin versión, timestamp ni tipo adecuado; escritura no atómica multi-cuenta** · *(DBA / AI Engineer)*
**Evidencia:** `ad_account.py:18` — `cached_narrative: Mapped[str]` (String sin longitud), truncado a 5000 chars en código (`engine.py:87`, `reports.py:52`). No hay `narrative_generated_at`, `narrative_model` ni `prompt_version`. En `engine.run_analysis` (`engine.py:44-91`) el `commit` es único al final para **todas** las cuentas; si la cuenta 3 lanza, se hace rollback de insights de 1 y 2.
**Por qué importa:** imposible saber cuándo/con qué modelo/prompt se generó una narrativa (clave si el LLM cambia — ver AI-4). El commit monolítico convierte un fallo de una cuenta en pérdida de trabajo de las demás. La truncación a 5000 corta briefs a media frase.
**Recomendación:** columna `Text`, más `narrative_generated_at`, `narrative_model`, `prompt_version`. Commit por cuenta (try/except por iteración). Si `cached_narrative` se vuelve rico, moverlo a tabla propia `ai_briefs` con historial.
**Esfuerzo:** M.

**D-5 🟡 — `Base.metadata.create_all` en startup coexiste con Alembic → drift de esquema** · *(DBA / Backend)*
**Evidencia:** `main.py:24-27` corre `create_all` en el lifespan. Existen migraciones (`alembic/versions/f7ffe513f0fb...`, `a70ab36472b6...`) pero `create_all` no aplica los `UniqueConstraint`/`Index` nuevos a una DB ya creada, y no versiona cambios.
**Por qué importa:** el esquema real de una DB con datos diverge del de las migraciones; los índices/constraints que se agreguen (D-1, D-2) no llegarán a prod si se confía en `create_all`. Irreproducible.
**Recomendación:** quitar `create_all` de prod; `alembic upgrade head` como paso de arranque/deploy. Dejar `create_all` solo para tests con SQLite.
**Esfuerzo:** S.

**D-6 🟢 — Integridad referencial inconsistente entre modelos** · *(DBA)*
**Evidencia:** `ad_account.py:13` (`organization_id` FK **sin** `ondelete`), `campaign_snapshot.py:11-12` (FKs sin `ondelete` a nivel DB; solo cascade ORM). Otros modelos sí usan `ondelete="CASCADE"` (`meta_credential.py:13`, `organization_user.py`). Mezcla.
**Por qué importa:** un borrado por SQL directo (o desde otra herramienta) orfana snapshots/insights porque el cascade vive solo en el ORM. Al desconectar una cuenta, la limpieza depende de pasar por SQLAlchemy.
**Recomendación:** homogeneizar `ondelete` a nivel DB en todas las FKs según la política de borrado deseada (cascade para snapshots/insights/actions de una cuenta).
**Esfuerzo:** S/M.

---

### 2.4 Motor de IA (AIEngine, 3 capas)

**AI-1 🔴 — Alucinación numérica sin control + narrativa mock con cifras inventadas servida al dashboard** · *(AI/ML Engineer)*
**Evidencia:** `llm.py:398-446` (`_generate_mock_narrative`) hardcodea números que **no** derivan de los datos: "CPL de **S/.1.45**", frecuencia "**3.4**", "**S/.12.00**", "CTR 0.22%". `reports.py:44-49` sirve esta narrativa al frontend cuando no hay `cached_narrative` y (con `MOCK_SERVICES=false` pero sin `OPENROUTER_API_KEY`, que es el estado del `.env`) `analyze_campaigns` cae al mock (`llm.py:28-29`). En modo real, el prompt (`llm.py:39-56`) pasa `raw_data` pero **nada verifica** que las cifras del LLM coincidan con la fuente.
**Por qué importa:** es un reporte que **decide gasto publicitario real**. Mostrar CPLs fabricados —o dejar que el LLM invente cifras plausibles— puede llevar a pausar una campaña sana o escalar una mala. Es el riesgo más peligroso del motor.
**Recomendación:** (1) el mock debe derivar sus números de `raw_data_summary`, nunca constantes. (2) En modo real, no pedir al LLM que **produzca** cifras: calcularlas determinísticamente en Python (KPIs, CPL, clasificación) e inyectarlas ya formateadas; el LLM solo redacta el texto alrededor. (3) Post-validación: extraer números del output y contrastar contra la fuente; descartar/marcar si divergen. 
**Esfuerzo:** M.

**AI-2 🔴 — Planner con presupuestos y targets hardcodeados a IDs de campañas mock** · *(AI/ML Engineer / Backend)*
**Evidencia:** `planner.py:91` (`old_budget = 70.0 if c_id == "camp_cable_gpt" else 50.0`), `:102` (`50.0 if c_id == "camp_vulcanizado" else 40.0`), `:135` nombre y `:146` región Lima hardcodeados. Los `planned_api_calls` apuntan a `endpoint: f"{c_id}"` (campaña) para presupuesto, pero `target_adset_id` nunca se setea (el budget suele vivir en el adset).
**Por qué importa:** si se activa el write-back (roadmap), estas acciones ejecutarían modificaciones con **presupuestos ficticios** y contra el **objeto equivocado** en Meta. Para un cliente real cuyo `campaign_id` no es `camp_cable_gpt`, `old_budget` cae al default arbitrario y el `new_budget` (±%) se calcula sobre una base falsa → cambio de gasto incorrecto.
**Recomendación:** leer el presupuesto real del último snapshot/estado de Meta antes de proponer; resolver adset objetivo; nunca derivar valores de IDs mock. Bloquear el write-back hasta cerrar esto (ver R-Roadmap).
**Esfuerzo:** M.

**AI-3 🟡 — Capa 2 se degrada silenciosamente con clientes nuevos; método estadístico naíf** · *(AI/ML Engineer)*
**Evidencia:** `statistics.py:44-46` — `if len(history) < 7: continue` (cliente nuevo con 3 días → **cero** análisis estadístico, sin avisar). Z-score con desviación **poblacional** y umbral fijo `std_dev > 0.1` (`:102-112`); ventanas fijas 3/14 días (`:49-50`).
**Por qué importa:** el cliente recién onboardeado no recibe la capa 2 y no hay señal de "datos insuficientes" (ni en insights ni en UI) — parece que "no pasa nada malo". El Z-score poblacional con n pequeño es sensible a outliers.
**Recomendación:** emitir un `AIInsight` INFO "histórico insuficiente para análisis estadístico (n<7)". Usar desviación muestral y, con n<14, un método robusto (MAD/mediana). Hacer ventanas y umbrales configurables (ver AI-5).
**Esfuerzo:** M.

**AI-4 🟡 — Sin versionado de prompts ni trazabilidad de qué generó cada narrativa** · *(AI/ML Engineer)*
**Evidencia:** prompts inline en `llm.py:39-56` y `gemini_client.py:36-43`; no hay `prompt_version`. `cached_narrative` no guarda modelo ni prompt (ligado a D-4). Además coexisten **dos** clientes LLM (`OpenRouterClient` y `GeminiClient`) con prompts distintos y el modelo por defecto difiere entre `config.py:29` (`gemini-3.5-flash`), `.env` (`gemini-2.5-flash`) y `organization.ai_model`.
**Por qué importa:** si se ajusta el prompt del Master Brief, no se puede saber qué versión produjo un brief histórico ni reproducir/comparar. La divergencia de modelos por defecto genera resultados inconsistentes entre entornos.
**Recomendación:** centralizar prompts en un módulo con constante `PROMPT_VERSION`; persistirla junto a la narrativa. Unificar el proveedor LLM (elegir OpenRouter **o** Gemini, no ambos). Fijar un único default de modelo.
**Esfuerzo:** M.

**AI-5 🟡 — Umbrales de reglas hardcodeados y atados a soles/Perú** · *(AI/ML Engineer / Product)*
**Evidencia:** `rules.py` — CPL 2× (`:118`), frecuencia 3.0 (`:131`), CTR 0.5% (`:144`), impresiones 200/500 (`:144`,`:157`), clamp de target CPL `1.50–10.00` (`:96`), CPL Instagram >5.0 (`:180`). Todo en S/. `tenant_rules/campaign_defaults.py` incluso lo admite ("TODO: refactorizar a tabla de BD por tenant").
**Por qué importa:** distintos verticales/clientes tienen economías de CPL muy distintas; una constante única genera falsos positivos/negativos. No escala a clientes fuera de Perú ni multi-moneda.
**Recomendación:** mover umbrales a config por organización/vertical (tabla `TenantRules`, ya anticipada). Normalizar por moneda de la cuenta.
**Esfuerzo:** M.

**AI-6 🟢 — Humano en el loop: correcto, pero el write-back ya existe parcialmente** · *(AI/ML Engineer / Product)*
**Evidencia:** las acciones nacen `PROPOSED` y requieren aprobación (`actions.py:68-76`). Pero `AIExecutor.execute_action` → `MetaCampaignModifier.modify` (`modifier.py:16-129`) **ya hace POST real** a Meta cuando `MOCK_SERVICES=false`. El `status.md` afirma que el write-back "todavía no existe".
**Por qué importa:** hay una brecha entre la doc y el código: aprobar una acción con `.env` actual (`MOCK_SERVICES=false`) **podría** disparar escrituras reales con los parámetros erróneos de AI-2. El human-in-loop está bien diseñado, pero la seguridad de lo que ejecuta no.
**Recomendación:** feature-flag explícito e independiente para write-back (distinto de `MOCK_SERVICES`), OFF por defecto, hasta cerrar AI-2. Ver UX-3.
**Esfuerzo:** S.

---

### 2.5 Async / Infraestructura (Celery, Redis, Docker)

**I-1 🔴 — Celery Beat: `schedule=86400.0` sin cron ni jitter → thundering herd contra Meta** · *(SRE / DevOps)*
**Evidencia:** `celery_app.py:20-33` — ambos jobs cada 86400s (no `crontab(hour=6)` como dice el roadmap). `schedule_all_syncs`/`schedule_all_analyses` (`tasks.py:25-55`) hacen fan-out inmediato de **todas** las cuentas/orgs a la vez.
**Por qué importa:** con 500 tenants, Beat encola 500 syncs simultáneos; el límite de Meta (~200 calls/hora por token) se revienta y, peor, la fecha de disparo deriva (86400s desde el arranque del contenedor, no "6am"). Un reinicio mueve la hora.
**Recomendación:** `crontab` real para las 6am. Escalonar con jitter (`countdown` aleatorio por cuenta) o una cola con rate-limit por token (`Task.rate_limit`). Presupuestar llamadas/hora por tenant.
**Esfuerzo:** M.

**I-2 🟡 — Worker único, concurrencia 2, sin autoscaling ni aislamiento de colas** · *(SRE)*
**Evidencia:** `docker-compose.yml:40-46` (`--concurrency=2`, un servicio). Todas las tareas (sync pesado, IA con LLM de 45s, creación) comparten el mismo pool.
**Por qué importa:** una corrida de IA lenta bloquea slots que necesita el sync; no hay separación entre trabajo interactivo (trigger manual del usuario) y batch (Beat). No escala horizontalmente por tipo.
**Recomendación:** colas dedicadas (`sync`, `ai`, `creation`) y workers separados con concurrencia propia; permitir réplicas. Considerar `acks_late` + `prefetch_multiplier=1` para tareas largas.
**Esfuerzo:** M.

**I-3 🟡 — docker-compose sin healthchecks/limits en servicios clave; typo en volumen de nginx** · *(SRE / DevOps)*
**Evidencia:** `docker-compose.yml` — redis, backend, celery-worker/beat y frontend **sin** healthcheck; ningún `deploy.resources.limits`. `:77` monta `./nginx/conf.d:/etc/nginx/rol_conf.d:ro` (**`rol_conf.d`** es un path inválido). No hay archivos `*.conf` en `nginx/` (el `default.conf` referenciado en `:78` puede no existir), pese a que la doc afirma que Nginx inyecta `X-Tenant-Slug`.
**Por qué importa:** sin healthchecks, `depends_on` no garantiza readiness (el worker arranca antes que backend/DB listos). Sin límites, un pico de memoria del worker puede tumbar el host. El typo + la ausencia de config de nginx sugieren que la inyección de `X-Tenant-Slug` por Nginx **no está operativa** — el header hoy lo pone el JS del cliente (`api.ts:24-31`), lo que refuerza S-8.
**Recomendación:** healthchecks para redis (`redis-cli ping`), backend (`/`) y worker (`celery inspect ping`); `mem_limit`/`cpus`. Corregir el mount a `/etc/nginx/conf.d`. Versionar la config real de Nginx y confirmar quién inyecta el slug.
**Esfuerzo:** M.

**I-4 🟡 — Observabilidad = `logging.basicConfig` + prints; sin Sentry/métricas/tracing** · *(SRE)*
**Evidencia:** `main.py:16-18` (basicConfig), `security.py:99` (print DEBUG), fallos de sync/IA solo van a logs de contenedor. No hay error tracking, structured logging, ni métricas de negocio (nº de syncs, coste LLM, tasa de fallo Meta).
**Por qué importa:** cuando A-2 deje jobs en FAILED en silencio, nadie se entera. No hay forma de medir el costo de OpenRouter por corrida ni de alertar ante degradación.
**Recomendación:** Sentry (backend + Celery + frontend), logging JSON con `tenant_id`/`job_id`, y métricas mínimas (Prometheus/StatsD o al menos una tabla de `job_metrics`). Alertas ante fallo de tarea.
**Esfuerzo:** M.

**I-5 🟢 — Costo de OpenRouter/LLM sin control ni presupuesto** · *(SRE / AI Engineer / Product)*
**Evidencia:** cada sync completado dispara `run_ai_analysis` (`tasks.py:75-77`), que llama al LLM por cuenta activa (`engine.py:74-78`, dos llamadas: narrativa + HTML). `/analyze-trigger` (`insights.py:50-60`) y `/reports/brief-data` (en frío) también invocan LLM. Sin rate-limit propio, sin caché de costo, sin tope.
**Por qué importa:** spamear `/analyze-trigger` o recargas de `/diagnostics` genera costo de LLM directo (y llamadas a Meta). Sin presupuesto por tenant, el gasto de IA es imprevisible.
**Recomendación:** rate-limit propio en `/analyze-trigger` y `/sync-trigger` (por org, p.ej. 1 cada N min vía Redis); reusar `cached_narrative` con TTL; contabilizar tokens/costo por org contra `max_budget`.
**Esfuerzo:** M.

---

### 2.6 Frontend / UX

**F-1 🟡 — Desajuste de `event_type` rompe el refresco por WebSocket del sync** · *(Frontend Engineer)*
**Evidencia:** el backend publica `event_type="sync_status"` en minúsculas (`notification_service.py:21`, `tasks.py`), pero el dashboard compara `lastEvent.event_type === "SYNC_STATUS"` en mayúsculas (`page.tsx:44`). Además el `sync_ad_account` real (`tasks.py:57-78`) **no publica ninguna notificación** WS; solo el chord mock (`sync_tasks.py:51-53`) notifica.
**Por qué importa:** el usuario aprieta "Sincronizar Ahora", `setSyncing(true)` (`page.tsx:61`) y **nunca** recibe el evento que lo resuelve → spinner "🔄 Sincronizando..." infinito aunque el sync haya terminado. Es un fallo de UX visible en el flujo principal.
**Recomendación:** unificar el nombre del evento (una constante compartida) y que el `sync_ad_account` real publique `sync_status` COMPLETED/FAILED al terminar. Añadir timeout de seguridad en el front.
**Esfuerzo:** S.

**F-2 🟡 — Manejo de errores/estados parcial; token solo validado por localStorage** · *(Frontend Engineer / UX)*
**Evidencia:** `diagnostics/page.tsx:14-30` traga errores con `console.error` sin estado de error visible (solo happy path o empty state). `layout.tsx:21-48` confía en `localStorage` sin llamar a `getMe` para validar el token. `api.ts:46-54` maneja 401 pero no 403/500 de forma diferenciada.
**Por qué importa:** si `getInsights`/`getBriefData` fallan por 500, el usuario ve "cargando…"→vacío sin saber que hubo error. Un token corrupto pero presente deja entrar al layout y falla request por request.
**Recomendación:** estados explícitos de error con retry en `/diagnostics` y dashboard; validar sesión con `getMe` al montar el layout; manejar 403 (mostrar "sin permisos") distinto de 401.
**Esfuerzo:** M.

**F-3 🟢 — Accesibilidad básica ausente** · *(UX/UI Designer)*
**Evidencia:** estilos inline en todo (`diagnostics`, `actions`, `settings`), estados comunicados **solo por color** (badges success/warning/danger sin texto/ícono redundante en varias tablas), botones-ícono sin `aria-label` (el `✕ Descartar` está ok, pero muchos no), contraste de `--text-muted` sobre fondo oscuro dudoso.
**Por qué importa:** un dashboard B2B que agencias muestran a sus clientes debería pasar WCAG AA mínimo; el color-only falla para daltónicos y el foco de teclado no está gestionado en el modal de rechazo (`actions/page.tsx:105-138`).
**Recomendación:** añadir texto/íconos redundantes a estados, `aria-label` a botones-ícono, focus-trap en el modal, revisar contraste. Considerar mover estilos a clases (hay Tailwind disponible).
**Esfuerzo:** M.

**F-4 🟢 — Inconsistencia visual entre dashboard (Tailwind) y resto (inline glass)** · *(UX/UI Designer / Frontend)*
**Evidencia:** `page.tsx` usa clases Tailwind (`bg-white dark:bg-gray-800`…) mientras `diagnostics/settings/actions` usan `style={{...}}` con variables CSS glass. Dos lenguajes visuales.
**Por qué importa:** el dashboard principal se ve "claro/Tailwind" y las otras páginas "glass oscuro" — incoherencia de marca en el producto que la agencia vende como premium.
**Recomendación:** unificar en un sistema (design tokens + componentes). El HTML de referencia ya define una paleta glass coherente reusable.
**Esfuerzo:** M.

---

### 2.7 Producto

**P-1 🔴 (de producto) — El aprobador de acciones no aclara qué pasa tras "Aprobar y Ejecutar"** · *(Product Manager / UX)*
**Evidencia:** `actions/page.tsx:196` botón "✅ Aprobar y Ejecutar"; `:240` muestra "Ejecutando llamadas API con pausas anti-detección"; `:250-254` muestra "ID meta: …" al completar. Pero según `status.md` el write-back "no existe" — y en realidad `modifier.modify` **sí** escribe si `MOCK_SERVICES=false` (AI-6). En mock, el usuario ve COMPLETED con un `campaign_id` ficticio.
**Por qué importa:** riesgo doble. En mock: el cliente **cree que pausó una campaña real en Meta** cuando no pasó nada → decisiones basadas en una ilusión. En real: pausa/ajusta de verdad con los parámetros erróneos de AI-2. Ambos son daños directos al cliente final de la agencia.
**Recomendación:** el estado y copy deben reflejar la verdad del sistema: si write-back está OFF, mostrar "Simulación — no se aplicó en Meta"; si ON, mostrar el objeto/valor exacto que se cambió y un enlace a Meta. Nunca mostrar un ID ficticio como si fuera real.
**Esfuerzo:** S/M.

**P-2 🟡 — `GET /reports/brief-data` está muy lejos del informe de referencia (brecha de datos, no de render)** · *(Product Manager)*
**Evidencia:** el brief actual (`reports.py:56-63`) devuelve solo `narrative` + `campaigns_data` (agregado 7 días a nivel campaña). El informe manual (`informe-rendimiento-jun2026.html`) tiene 8 secciones que requieren datos que **el sync real no captura** (ver Anexo). El pipeline que sí los pediría (`meta_sync_service.generate_sync_plan` con adsets, breakdowns, adimages, custom_audiences) está **stubbeado** (`meta_insights_service.save_query_results` no persiste; `master_brief_generator.generate_brief` retorna estructura vacía).
**Por qué importa:** el valor que la agencia vende es ese informe detallado. Hoy la app entrega ~2 de las 8 secciones. La brecha principal es de **captura de datos**, no de diseño de PDF — por eso hay que resolverla antes del generador de PDF del roadmap.
**Recomendación:** ver Anexo y Roadmap. Priorizar capturar adset/creativo/dispositivo/género/geografía en el modelo de datos.
**Esfuerzo:** L.

---

## 3. Quick Wins (esfuerzo S, alto impacto) — hacer primero

| # | Hallazgo | Acción | Impacto |
|---|----------|--------|---------|
| QW-1 | S-1 | Rotar `SECRET_KEY`/`ENCRYPTION_KEY`, dejar solo placeholders en `.env.example` | Cierra fuga total de tokens/sesiones |
| QW-2 | S-2 | Añadir auth + validación de tenant a `/api/v1/sync/*` (o borrar el router si es legacy) | Cierra IDOR y endpoint no autenticado |
| QW-3 | A-1 | `autodiscover_tasks(["app.workers"])` o ampliar `include` | Desbloquea creación de campañas y sync alterno |
| QW-4 | S-7 | Eliminar el `print(DEBUG)` de `security.py:99` | Corta fuga en logs |
| QW-5 | F-1 | Unificar `event_type` y publicar WS al fin del sync real | Arregla spinner infinito del flujo principal |
| QW-6 | D-1/D-2 | Unique `(ad_account_id,campaign_id,date)` + índice `(ad_account_id,date)` | Evita duplicados que inflan diagnósticos |
| QW-7 | S-4 (parcial) | Quitar User-Agent falso de `rate_limiter.py` (usar el honesto) | Reduce exposición inmediata a ToS |
| QW-8 | AI-6/P-1 | Flag `ENABLE_META_WRITEBACK=false` separado de `MOCK_SERVICES` | Evita escrituras reales accidentales |

---

## 4. Deuda Técnica Seria (M/L, bloquea escalar)

- **A-3 (L)** — Dos stacks Meta paralelos: consolidar en uno. Bloquea razonar sobre correctness y mantener.
- **A-2 + I-1 + I-2 (M)** — Reintentos/DLQ, cron real con jitter y colas por tipo. Bloquea el paso a Celery Beat con muchos tenants.
- **P-2 + Anexo (L)** — Captura de datos a nivel adset/creativo/breakdowns. Bloquea el producto que la agencia vende.
- **AI-1 + AI-2 (M)** — Determinismo numérico y saneo del planner. Bloquea write-back seguro.
- **D-3 (M/L)** — Particionado/archivado de `campaign_snapshots`. Bloquea escalar la "tabla más pesada".
- **S-6 + S-8 (M)** — Ciclo de vida de tokens Meta y "tenant activo" real. Bloquea onboarding de agencias multi-cuenta/multi-usuario.

---

## 5. Roadmap Priorizado

### Esta semana — Contención (seguridad y correctness)
1. QW-1…QW-8 completos.
2. S-3 (auth en WebSocket) y S-4 (desmontar anti-detección; backoff por `x-business-use-case-usage`).
3. A-2 mínimo: distinguir errores transitorios/permanentes + alerta en fallo (aunque sea log estructurado + Sentry).
4. Congelar el write-back real detrás del flag hasta AI-2 (no activar aunque el `.env` tenga `MOCK_SERVICES=false`).

### Este mes — Consolidación
5. A-3: unificar cliente Meta y modelo de credenciales; borrar Stack B stubbeado.
6. D-5 (Alembic como fuente de verdad) + D-1/D-4/D-6 en migraciones.
7. AI-1 (determinismo numérico) y AI-4 (versionado de prompts, un solo proveedor LLM, un solo default de modelo).
8. I-1/I-2/I-3/I-4: cron real con jitter, colas por tipo, healthchecks/limits, Sentry + logging estructurado.
9. F-1/F-2/P-1: arreglar feedback de sync, estados de error, y honestidad del aprobador de acciones.

### Próximo trimestre — Valor de producto
10. **Captura de datos completa** (P-2/Anexo): adset, creativo/ad, breakdowns de dispositivo/género/geografía; nuevas tablas o extensión de snapshots. **Prerrequisito del generador de PDF.**
11. AI-2/AI-6: write-back seguro (presupuestos y objetos reales, adset resuelto) con el flag activable por tenant.
12. Celery Beat 6am productivo con presupuesto de llamadas por token (I-1).
13. D-3: particionado/archivado. S-6/S-8: ciclo de vida de tokens y tenant activo.
14. Recién aquí: **generador de PDF** del Master Brief (queda para el prompt de diseño de export).

> **Veredicto sobre el orden del equipo (write-back → Beat → PDF):** incorrecto tal cual. El write-back es lo más peligroso de activar hoy (AI-2, P-1) y debe ir **después** de seguridad, consolidación y saneo del planner. El PDF no debe empezar hasta cerrar la brecha de captura de datos (si no, se diseñaría un export sobre datos que no existen). Beat es razonable para "este mes" **solo** tras A-2/I-1.

---

## 6. Anexo — Brecha de Completitud del Reporting

Comparación sección por sección entre `informe-rendimiento-jun2026.html` (referencia manual) y lo que la app puede entregar hoy vía `sync.py` (real) + modelos + `reports/brief-data` + frontend.
Estados por capa: **Descarga** (¿se trae de Meta y se guarda?) · **Cálculo** (¿se agrega/analiza?) · **UI** (¿se muestra en el front?).

| Sección del informe de referencia | Descarga | Cálculo | UI | Brecha principal |
|---|---|---|---|---|
| **Resumen ejecutivo + KPIs** (invertido, alcance, conversaciones, mejor costo/conv, CTR prom) | Parcial | Parcial | Sí | `spend/clicks/conversions/ctr` sí; **alcance (`reach`) y "conversaciones" (msg started) NO** se capturan en `campaign_snapshot` (solo `conversions` genérico). KPIs de reach/conversaciones faltan en las 3 capas. |
| **Hallazgos + recomendaciones en lenguaje simple** | N/A | Sí | Sí | Cubierto por narrativa LLM — pero con riesgo AI-1 (cifras). |
| **Alertas rojo/verde** (spend cap, campañas sin conversión) | Sí | Sí | Sí | Existe en `rules.py` (spend cap, conversiones=0) y se muestra como insights. **Es lo más completo.** |
| **Ranking de campañas por CPL** | Sí | Sí | Sí | `campaigns/recent` + tabla lo cubren a nivel campaña. |
| **Detalle a nivel adset** | **No** | No | No | El sync real no guarda adsets (`meta_sync_service` los pediría pero está stubbeado). Falta tabla `adset_snapshots`. |
| **Detalle a nivel anuncio/creativo** | **No** | No | No | No se capturan ads ni creativos con rendimiento. Falta modelo. |
| **Tendencia semanal con diagnóstico de causas** | Parcial | Parcial | Parcial | Hay snapshots diarios y `campaigns/{id}/history`, pero no hay agregación semanal ni "diagnóstico de causa" estructurado. |
| **Breakdown edad/género** | Parcial | No | No | `demographics_breakdown` guarda **solo edad** (`sync.py:180-185`), **sin género**. No se calcula ni se muestra. |
| **Breakdown plataforma/placement** | Sí | Parcial | No | `placements_breakdown` se guarda y `rules.py` lo usa para insights, pero **no se expone** en el brief/UI como sección. |
| **Breakdown dispositivo** | **No** | No | No | No se captura `impression_device`. |
| **Breakdown geografía** | **No** | No | No | No se captura `region`. |
| **Audiencias personalizadas + oportunidades** | **No** | No | No | `customaudiences` solo en el plan stubbeado; sin modelo ni UI. |
| **Inventario de creativos/imágenes con rendimiento** | **No** | No | No | `adimages` solo en el plan stubbeado; sin modelo ni UI. |
| **Parámetros óptimos recomendados para nuevas campañas + justificación** | **No** | Parcial | No | Existe `tenant_rules/campaign_defaults.py` (constantes) y el planner sugiere `CREATE_CAMPAIGN` con params, pero no hay un cálculo de "parámetros óptimos" derivado del histórico ni sección dedicada. |

**Lectura para el roadmap:** de 8 secciones "ricas" del informe, **5 no tienen ni el dato capturado** (adset, ad/creativo, dispositivo, género, geografía, audiencias, inventario). La palanca #1 es **ampliar la captura y el modelo de datos** (nuevas tablas `adset_snapshots`, `ad_snapshots`, `breakdown_*` o un `snapshot_breakdowns` polimórfico), reactivando el plan de queries que ya está esbozado en `meta_sync_service.generate_sync_plan` pero conectándolo a persistencia real. El diseño del export/PDF debe abordarse **después**, en su propio prompt, sobre estos datos ya disponibles.

---

*Fin del informe. Los hallazgos marcados 🔴 en Seguridad (S-1, S-2, S-3), Arquitectura (A-1) y Datos (D-1) deben resolverse antes de cualquier feature del roadmap del equipo.*
