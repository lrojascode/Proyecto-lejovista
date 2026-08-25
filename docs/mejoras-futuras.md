# Mejoras futuras

Ideas y capacidades pendientes, documentadas para no perderlas. No son bloqueantes.

## App de Meta por cliente (multi-app)

**Estado:** ✅ implementado el 2026-08-25 (commit de `meta_credential.app_id`).
Se guarda **por credencial** (`meta_credentials.app_id` + `app_secret_encrypted`,
cifrado con Fernet) y no por organización, que es lo que este documento proponía:
lo que hace falta saber no es "qué app usa este cliente" sino "qué app emitió
ESTE token". Una organización puede alojar la cuenta de un cliente conectada con
la app de la agencia y la de otro con la suya, y con un solo valor por
organización una de las dos queda mal — y "mal" significa que `debug_token`
falla, la revisión diaria lo lee como token inválido y marca la cuenta EXPIRED.
`NULL` = la app global de la agencia, que sigue siendo el caso normal.

Lo que sigue es el análisis original, que se conserva porque explica el porqué.

**Estado original:** documentado, no implementado. Solo hace falta si un cliente exige usar
**su propia app de Meta** (su App ID/Secret), en vez de la app de la agencia.

**Contexto — cómo funciona hoy:**
- `META_APP_ID` / `META_APP_SECRET` (env global) = **una sola app de Meta** (la de
  Agencia Kibo). Se usa solo para el *token exchange* (canjear el token corto por
  uno long-lived de ~60 días) en `backend/app/services/meta_ads/token_exchange.py`.
- El **token de cada cliente** vive por organización, cifrado, en
  `backend/app/models/meta_credential.py` (`MetaCredential.access_token_encrypted`,
  `token_expires_at`, `organization_id`). Cada tenant (ITS, etc.) tiene su propio
  token long-lived, aislado, con su propia expiración.
- Modelo estándar de agencia: una app + *partner access* a los Business Manager de
  cada cliente. ITS ya obtiene su propio token de larga duración vía la app de la
  agencia — **no necesita app propia**. El App ID/Secret único cubre N clientes.

**Cuándo haría falta el cambio:** un cliente que insiste en tokens emitidos por
**su** app de Facebook (ej. un System User token de la app de ITS). Ahí el código
actual no sirve porque `exchange_for_long_lived_token` usa el `settings.META_APP_ID/
SECRET` global hardcodeado.

**Qué implementar (cambio acotado):**
1. Añadir `app_id` y `app_secret` **por organización** — en `Organization` o
   `MetaCredential` (el secret cifrado con Fernet, como el access token). Migración
   Alembic para las columnas nuevas.
2. Que `exchange_for_long_lived_token(...)` reciba/resuelva los credenciales de la
   org (si existen) y haga fallback al `settings.META_APP_ID/SECRET` global cuando
   la org no tenga app propia. Ajustar los llamadores en
   `backend/app/api/v1/organizations.py` (~línea 382) para pasar el contexto de org.
3. UI en Settings para que un cliente con app propia cargue su App ID/Secret.

**Recomendación:** mantener la app única de la agencia hasta que un cliente lo pida
contractualmente. La mayoría de agencias operan con una sola app + partner access.

## Multi-tenant por subdominio wildcard (onboarding de varios clientes)

**Estado:** documentado, no implementado. Necesario cuando se onboardee el 2º
cliente (ej. ITS) con su propio espacio aislado.

**Contexto:** la app aísla tenants **por subdominio** (el middleware extrae el
slug de `<slug>.BASE_DOMAIN` e inyecta `X-Tenant-Slug`; ver
`frontend/middleware.ts` y `frontend/lib/config.ts`). Hoy en prod solo existe el
dominio base `lejovista.agenciakibo.com` — sirve para un tenant, pero no para dar
a cada cliente su propia URL.

**Qué implementar para `its.lejovista.agenciakibo.com`, etc.:**
1. **DNS wildcard:** registro `*.lejovista.agenciakibo.com` → IP del VPS.
2. **Coolify:** dominio **wildcard** en el servicio `frontend`
   (`*.lejovista.agenciakibo.com`) con su certificado (Let's Encrypt wildcard vía
   DNS-01, o Coolify por-host). El `backend` ya acepta el `X-Tenant-Slug`.
3. **CORS:** ya cubierto — la regex se deriva de `BASE_DOMAIN` e incluye
   `(.*\.)?` (subdominios), así que los orígenes `<tenant>.lejovista.agenciakibo.com`
   pasan sin cambios (ver `backend/app/main.py`).
4. Flujo de alta de tenant: signup por slug + (a futuro) invitaciones.
