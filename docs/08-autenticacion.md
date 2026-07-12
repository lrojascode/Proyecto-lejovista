# 08 · Autenticación y Autorización

Implementada en `app/utils/security.py`, `app/api/v1/auth.py` y consumida como dependencias de FastAPI.

## 1. Esquema general

- **JWT** firmados con `SECRET_KEY` (algoritmo `HS256`).
- Dos tipos de token: **access** (default 1440 min = 24 h) y **refresh** (default 10080 min = 7 días), configurables con `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_MINUTES`.
- Contraseñas hasheadas con **bcrypt** vía `passlib` (`CryptContext(schemes=["bcrypt"])`).
- Claims del token: `sub` (user id), `type` (`access`/`refresh`), `jti` (UUID, para revocación), `exp`, y opcional `org` (organización activa de la sesión).

## 2. Flujo de sesión

```mermaid
sequenceDiagram
    participant U as Usuario
    participant API as /auth
    participant R as Redis (blocklist)

    U->>API: POST /auth/signup o /login
    API-->>U: access + refresh (+ user)
    Note over U: guarda ambos en localStorage

    U->>API: request con Bearer access
    alt access expiró (401)
        U->>API: POST /auth/refresh (refresh)
        API->>R: revoca jti del refresh usado (rotación, S-9)
        API-->>U: nuevo access + nuevo refresh
    end

    U->>API: POST /auth/logout (refresh)
    API->>R: revoca jti (blocklist con TTL)
```

- **Signup** (`/auth/signup`): valida slug reservado y unicidad de email; **funda una organización nueva** (`Organization` + `User` + `OrganizationUser(ADMIN)`). Rate-limit 5/hora por IP. (S-1) Si el slug ya pertenece a una organización existente, devuelve 400 — no existe flujo de invitación, así que unirse a una org por slug ya no es posible desde signup: antes, cualquiera que conociera/adivinara el slug de un tenant ajeno (visible en el subdominio) podía registrarse contra él y quedaba vinculado como **ADMIN** de esa organización sin verificación alguna.
- **Login** (`/auth/login`): verifica password; emite tokens con la primera organización como activa. Rate-limit 10/5min por IP.
- **Refresh** (`/auth/refresh`): valida `type=="refresh"` y que el `jti` no esté revocado; **rota** (revoca el usado, emite nuevo par) — mitiga reuso de tokens robados.
- **Logout** (`/auth/logout`): revoca el `jti` del refresh (los access caducan solos).

## 3. Revocación (blocklist en Redis)

- `revoke_jti(jti, ttl)` → `SET revoked:{jti} 1 EX ttl`.
- `is_revoked(jti)` → lectura. **Fail-open**: si Redis no responde, no bloquea (se prioriza disponibilidad sobre revocación estricta).
- Los **access tokens no se revocan** activamente; se confía en su expiración corta relativa. La blocklist aplica a los refresh.

## 4. Dependencia `get_current_user`

Es el interceptor de autorización principal:

1. Decodifica el JWT del header `Authorization` (`oauth2_scheme`, `auto_error=False`).
2. Carga el `User` con sus `organizations` (`selectinload`).
3. Resuelve la **organización activa** (`S-8`):
   - Si viene `X-Tenant-Slug`: busca la org por slug y **exige** que el usuario pertenezca (403 si no).
   - Si no: usa el claim `org` del token si el usuario pertenece a esa org.
   - Fija `user._active_org_id`, que alimenta la propiedad `User.organization_id`.

Esto garantiza que un usuario con acceso a varias organizaciones no pueda operar sobre una a la que no pertenece, aunque manipule el subdominio.

## 5. Dependencia `get_active_ad_account_id`

Resuelve la **cuenta publicitaria** de la request (aislamiento multi-cuenta dentro de una org):

- Con header `X-Ad-Account-Id`: valida que la cuenta pertenezca a la org y no esté archivada (403 si no).
- Sin header: `None` si la org tiene 0 cuentas; auto-selecciona si tiene exactamente 1; **400 `MULTIPLE_AD_ACCOUNTS_NO_SELECTION`** si tiene 2+ (obliga a elegir explícitamente en vez de "la primera" en silencio).

## 6. Autorización WebSocket

`/ws/notifications/{tenant_id}` (`app/api/v1/ws.py`):

- El handshake exige `?token=<JWT>`. `_authenticate_ws` decodifica el token, carga el usuario y comprueba que `tenant_id` (= `organization_id`) esté entre sus organizaciones.
- Si falla, cierra con código `4401` (violación de política) sin aceptar la conexión.

## 7. Roles y autorización — `require_role` (S-1)

- `OrganizationUser.role`: `ADMIN` > `OPERATOR` > `VIEWER` (jerarquía, no categorías independientes). Hoy solo `signup` asigna un rol (siempre `ADMIN`, al fundar la organización) — no existe todavía un flujo de invitación que agregue miembros con `OPERATOR`/`VIEWER`.
- `require_role(min_role)` (`app/utils/security.py`) es una dependencia FastAPI que reconsulta `OrganizationUser.role` en cada request (no queda cacheada en el JWT) para la organización **activa** de la sesión (misma resolución que `get_current_user`, `S-8`) y exige ese rol o uno de rango superior — `403` si no. Sin fila de membresía (dato corrupto/borrado) también deniega, no asume acceso.
- Endpoints protegidos:

  | Rol mínimo | Endpoints |
  |---|---|
  | `ADMIN` | `PUT /organizations/my`, `POST /organizations/my/ad-accounts` (conectar), `.../ad-accounts/{id}/archive`, `.../restore` |
  | `OPERATOR` | `PATCH /organizations/my/pages/{id}/whatsapp`, `POST /campaigns/sync-trigger`, `POST /campaigns/upload-image`, `POST /campaigns/risk-preview`, `POST /campaigns/` (crear), `POST /insights/{id}/dismiss`, `POST /insights/analyze-trigger`, `POST /actions/{id}/approve` |

  El resto de endpoints (lecturas `GET`) no están gateados por rol: cualquier miembro autenticado de la org puede ver dashboard, reportes, insights e historial — el filtro es solo de **escritura**.

## 8. Token temporal (in-memory)

`create_temp_token` / `consume_temp_token` mantienen tokens efímeros en un dict en memoria del proceso. No se observan usos activos en los routers; es un mecanismo latente (no sobrevive a reinicios ni funciona con múltiples réplicas). Ver [15](15-deuda-tecnica.md).

## 9. Superficie de credenciales

- El **token de Meta** nunca es una credencial de sesión del usuario; se cifra en `MetaCredential` y se usa solo server-side. Ver [09-seguridad](09-seguridad.md).
- El frontend guarda `token`/`refresh_token` en `localStorage` (no cookies httpOnly) — implicaciones de XSS descritas en [09](09-seguridad.md).
