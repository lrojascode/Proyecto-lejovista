# 09 · Seguridad

Este documento reúne los controles de seguridad **presentes en el código** y sus límites. Ver riesgos priorizados en [15-deuda-tecnica](15-deuda-tecnica.md).

## 1. Modelo de amenazas resumido

El activo más sensible es el **token de acceso a Meta Ads**: permite leer datos y, potencialmente, **gastar dinero real** creando/modificando campañas. El diseño gira en torno a proteger ese token y a evitar escrituras accidentales o cruzadas entre tenants.

## 2. Cifrado de credenciales en reposo

- `MetaCredential.access_token_encrypted` se cifra con **Fernet** (`cryptography`) usando `ENCRYPTION_KEY`.
- Cifrado/descifrado solo en memoria del backend (`get_access_token`).
- En logs, el token se **enmascara** (`APICallLog` reemplaza `access_token` por `***`, igual que `MetaAPIRateLimiter`).
- `ENCRYPTION_KEY` y `SECRET_KEY` **no** deben committearse (documentado en `.env.example`; el `.env` real está gitignored).

## 3. Gating de escrituras a Meta (defensa en profundidad)

Tres flags independientes controlan si una escritura es real o simulada:

```mermaid
flowchart TD
    A["¿MOCK_SERVICES = true?"] -->|sí| SIM["SIMULAR (no toca Meta)"]
    A -->|no| B["¿ENABLE_META_WRITEBACK = true?"]
    B -->|no| SIM
    B -->|sí| C["¿WRITEBACK_ACCOUNT_ALLOWLIST vacío?"]
    C -->|sí| REAL["ESCRITURA REAL (todas las cuentas)"]
    C -->|no| D["¿cuenta ∈ allowlist?"]
    D -->|sí| REAL
    D -->|no| SIM
```

- `settings.SIMULATE_META_WRITES` (global) y `settings.simulate_meta_writes_for(ad_account_id)` (`F-2`, por cuenta piloto).
- Las **lecturas** dependen solo de `MOCK_SERVICES`; las **escrituras** requieren además `ENABLE_META_WRITEBACK` y, si hay allowlist, estar en ella.
- Regla de oro: las campañas se crean siempre `PAUSED` (`TENANT_CAMPAIGN_RULES["default_status"]`); la activación es manual en Ads Manager.
- El resultado de una modificación marca `simulated: true/false` para que el frontend no asuma que se aplicó de verdad (`P-1`).

## 4. Aislamiento multi-tenant

- Cada endpoint filtra por `current_user.organization_id` (organización activa validada, `S-8`).
- `get_active_ad_account_id` valida que la cuenta pertenezca a la org y rechaza cuentas ajenas con 403 **sin distinguir** "no existe" de "es de otra org" (no filtra qué cuentas existen).
- El WebSocket valida pertenencia al tenant antes de aceptar la conexión.
- Los `404` en recursos (insight/acción/draft de otra org) no revelan existencia.

## 5. Reglas de seguridad financiera en modificaciones

En `MetaCampaignModifier`:
- **Presupuesto**: rechaza subir a más del **150%** del actual.
- **Placements**: siempre incluye `facebook_reels` (`required_placements`) y **nunca** posiciones de Instagram.
- **Targeting**: siempre mantiene la región por defecto (Lima, key `2771`).
- **Backup + rollback**: antes de modificar guarda `backup_state`; ante fallo, `AIExecutor` intenta rollback automático. El `backup_state` se **reasigna** (no muta in-place) para que SQLAlchemy lo persista (`A-3`).
- **Anti-bloqueo de creación**: máx. 5 campañas nuevas por org / 24 h (playbook §2), y `countdown=40s` entre pasos de la Saga.

## 6. Rate limiting y throttling

| Mecanismo | Dónde | Propósito |
|-----------|-------|-----------|
| `enforce_rate_limit` (Redis INCR + TTL) | signup 5/h, login 10/5min por IP | Fuerza bruta / spam de registro |
| `enforce_cooldown` (Redis SET NX EX) | sync 60 s, análisis 120 s por org | Evitar spam de operaciones costosas (Meta/LLM) |
| `MetaAPIRateLimiter` | llamadas a Meta | Backoff por `x-business-use-case-usage`, pausas preventivas, reintentos |

Todos los limitadores basados en Redis son **fail-open**. (S-4) La IP del cliente se toma de `X-Forwarded-For` solo si el peer directo de la conexión es una IP privada/loopback (Nginx, dentro de la red interna de docker); si no, se usa el peer directo y se ignora el header — evita que alguien evada el límite con un header inventado si el backend quedara expuesto sin el proxy. Detalle en §9e más abajo.

## 7. CORS y exposición

- CORS con orígenes fijos + regex (`localhost`, `lrojas.dev`) y `TUNNEL_ORIGIN_REGEX` opcional; `allow_credentials=True`.
- `ENABLE_API_DOCS` debe ponerse en `false` al exponer la app públicamente (los docs revelan el esquema completo sin auth).
- `User-Agent` honesto que identifica la app ante Meta (cumplimiento de Platform Terms); **no** se falsifica un navegador ni se simula comportamiento humano.

## 8. Manejo de tokens de Meta expirados

- `_maybe_mark_expired` (`services/meta_ads/sync.py`) detecta `OAuthException code 190` y marca cuenta + credencial como `EXPIRED` (`S-6`), para que el frontend pida reconectar.

## 9. Validación de subida de archivos

`POST /campaigns/upload-image`: whitelist de tipos (`image/jpeg|png|webp`), límite 8 MB, nombre generado con UUID, guardado en `MEDIA_ROOT/{org_id}/`. `MetaAssetRepository` valida con `os.path.realpath` que la ruta no escape de `MEDIA_ROOT` (anti path-traversal).

## 9b. RBAC (S-1) — resuelto

`require_role(min_role)` (ver [08-autenticacion §7](08-autenticacion.md)) protege todos los endpoints de escritura de organizaciones/cuentas, campañas, insights y acciones con la jerarquía `ADMIN > OPERATOR > VIEWER`. Cierra también una escalada de privilegios relacionada: signup ya no permite unirse a una organización existente por slug (antes vinculaba al nuevo usuario como `ADMIN` de esa org sin invitación).

## 9c. Tokens fuera de `localStorage` (S-2) — resuelto

- El **access token** vive en memoria del módulo `frontend/lib/api.ts` (variable de módulo, no persistida). El **refresh token** viaja **solo** como cookie `HttpOnly` + `SameSite=Lax` (+ `Secure` cuando la conexión es HTTPS) fijada por el backend (`set_refresh_cookie`, `app/utils/security.py`) — nunca en el cuerpo JSON de `/auth/login`, `/auth/signup` ni `/auth/refresh`. Detalle completo del mecanismo y de la cookie en [08-autenticacion §2](08-autenticacion.md).
- Reduce la superficie de robo de sesión por XSS: un script inyectado ya no puede leer el refresh token con `document.cookie` (bloqueado por `HttpOnly`) ni encontrarlo en `localStorage`. El access token sigue siendo legible en memoria mientras la pestaña esté abierta (inevitable — la sesión lo necesita para operar), pero no sobrevive a un reload ni queda en almacenamiento persistente.
- **Limitación conocida**: en desarrollo local con el esquema de "2 subdominios" (`*.localhost`), Chrome no comparte "site" entre `<slug>.localhost` y `localhost`, así que el refresh vía cookie falla tras un reload en ese modo específico (no ocurre en producción con dominio real, ni en modo túnel/single-host). Ver detalle y workaround en [08-autenticacion](08-autenticacion.md).

## 9d. Blocklist de JWT fail-closed para el refresh (S-3) — resuelto

- `is_revoked` (`app/utils/security.py`) ahora es **fail-closed**: si Redis no responde al consultar si un `jti` está revocado, levanta `RevocationCheckUnavailable` en vez de asumir "no revocado". `POST /auth/refresh` traduce esto en **503** — rechaza la renovación de sesión mientras dure la caída de Redis, en vez de aceptar un refresh potencialmente robado/rotado porque no había forma de verificarlo.
- Alcance deliberadamente acotado: solo `is_revoked` (la lectura de la blocklist) cambió de fail-open a fail-closed. `revoke_jti` (la escritura, en logout/rotación) sigue siendo best-effort — si Redis cae justo al revocar, el token queda vivo hasta su expiración natural; endurecer esto tendría el efecto secundario de que un `logout` pueda fallar visiblemente por una caída de Redis, un trade-off de UX que no se tomó en este cambio. El resto de la capa Redis (rate limiting, cooldown, cache) sigue fail-open a propósito — es soporte, no la fuente de verdad de la sesión.
- **Hallazgo real durante la implementación**: al hacer este cambio, varios tests de `/auth/refresh` empezaron a fallar — resultó que corrían con Redis genuinamente inalcanzable en el entorno de test (fail-open lo ocultaba en silencio: is_revoked devolvía `False` y el refresh se aceptaba igual). Se agregó un fake Redis en memoria por test (mismo patrón que `tests/test_throttle.py`) a los tests que necesitan que la revocación funcione de verdad.

## 9e. `X-Forwarded-For` solo desde un peer confiable (S-4) — resuelto

- `get_client_ip` (`app/api/v1/auth.py`) ya no confía en `X-Forwarded-For` incondicionalmente. Solo lo usa si el peer directo de la conexión (`request.client.host`) es una IP privada/loopback — en este deployment, el backend nunca se publica al host/internet (`expose`, no `ports`, en `docker-compose.prod.yml`); el único que puede hablarle directo es Nginx, dentro de la red interna de docker. Si el peer directo fuera una IP pública (backend expuesto por error, sin el proxy), se ignora el header y se usa el peer directo — evita que cualquiera evada el rate limit de signup/login mandando un `X-Forwarded-For` inventado.
- Tests en `backend/tests/test_client_ip.py`.

## 9f. Prompt injection desde datos de Meta (S-5) — resuelto

- El texto que se interpola como **texto libre** en los prompts del LLM (`app/services/ai/llm.py`) — nombre de organización/cuenta en el Master Brief; `product_name` y nombre/copy de la campaña ganadora en la generación de copy — ahora se envuelve en delimitadores explícitos (`_delimit_untrusted`, marcadores `<<<DATO_EXTERNO_NO_CONFIABLE>>>...<<<FIN_DATO_EXTERNO>>>`) antes de mandarlo al modelo. El `system_prompt` de ambas llamadas incluye una regla explícita: todo lo que esté entre esos delimitadores es **dato**, nunca una instrucción, sin importar lo que diga.
- El resto de los datos del brief (campañas, KPIs, segmentación) viaja dentro de bloques `json.dumps(...)` — el escapado de JSON ya evita que un nombre de campaña "rompa" la estructura del prompt; el delimitador se añadió específicamente donde faltaba esa protección estructural (interpolación como texto plano).
- No es sanitización (no se filtran caracteres): campañas y copy pueden tener emojis, puntuación y texto libre legítimo. Es "spotlighting" — marcar la frontera entre instrucción y dato para que el modelo la respete.
- Tests en `backend/tests/test_campaign_copy_llm.py::test_suggest_campaign_copy_delimits_untrusted_content` y `backend/tests/test_master_brief_v2.py::test_org_and_account_name_delimited_against_prompt_injection`.

## 10. Límites conocidos (ver detalle en [15](15-deuda-tecnica.md))

Ninguno pendiente en la categoría de seguridad — ver [15-deuda-tecnica §1](15-deuda-tecnica.md) para el historial completo (S-1 a S-5, todos resueltos) y el resto de categorías (arquitectura, rendimiento, operación, producto) con hallazgos aún abiertos.
