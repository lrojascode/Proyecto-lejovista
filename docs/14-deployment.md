# 14 · Deployment

## 1. Topología de contenedores

Una sola imagen de **backend** se reutiliza para el API, los 3 workers y beat (solo cambia el `command`). El **frontend** es una imagen Next.js standalone. En **local** Nginx es el punto de entrada; en **producción** el proxy + TLS los pone **Coolify** (no hay servicio `nginx` en el compose de prod — ver [§3](#3-producción-coolify)).

| Servicio | Imagen | Rol |
|----------|--------|-----|
| `db` | `postgres:16-alpine` | Base de datos |
| `redis` | `redis:7-alpine` (`--requirepass`, `--maxmemory 256mb`) | Broker, cache, pubsub |
| `migrate` | ídem imagen de `backend` | Contenedor de un solo uso: `alembic upgrade head`, corre y termina antes que `backend` (`O-2`) |
| `backend` | `proyectolejovista-backend` | API (uvicorn) |
| `celery-worker-sync` | ídem | Cola `sync` (concurrency 2) |
| `celery-worker-ai` | ídem | Cola `ai` (concurrency 1) |
| `celery-worker-creation` | ídem | Colas `creation,celery` (concurrency 1) |
| `celery-beat` | ídem | Cron |
| `frontend` | `proyectolejovista-frontend` | Next.js standalone |
| `nginx` | `nginx:alpine` | Reverse proxy + TLS **solo en local**; en prod lo reemplaza Coolify |

## 2. Desarrollo local (`docker-compose.yml`)

- **Build local**: `backend` se construye una vez; workers/beat reutilizan `image: proyectolejovista-backend:local`.
- **Hot reload**: el código se monta (`./backend:/app`); uvicorn y los workers usan `--reload`/`watchfiles`.
- **Healthchecks** en db, redis, backend y workers (con `start_period`).
- **Límites de recursos** (`mem_limit`/`cpus`) por servicio.
- Puertos de datos publicados solo en `127.0.0.1`.

```bash
cp .env.example .env      # generar SECRET_KEY y ENCRYPTION_KEY
docker compose up -d
```

`alembic upgrade head` ya no es un paso aparte (`O-2`, resuelto): el servicio `migrate` lo corre automáticamente y `backend` espera a que termine (`depends_on: migrate: condition: service_completed_successfully`) antes de arrancar.

## 3. Producción (Coolify)

lejovista corre en producción como recurso **Docker Compose de Coolify** sobre el VPS, usando [`docs/infra/coolify/lejovista.compose.yml`](infra/coolify/lejovista.compose.yml) (idéntico al stack local pero **sin `nginx`**: el proxy + TLS los pone Coolify). Dominios: `lejovista.agenciakibo.com` (frontend) y `api.lejovista.agenciakibo.com` (backend).

Filosofía: **no se construye nada en el VPS**. Las imágenes se compilan en CI (GitHub Actions) y se publican en GHCR; Coolify solo hace `pull`.

**Flujo de despliegue** (detalle y rollback en [`docs/infra/deploy-runbook.md`](infra/deploy-runbook.md)):

1. Push a `main` → CI corre los tests.
2. CI verde → `deploy.yml` construye y publica las imágenes en GHCR con dos tags: `${sha}` inmutable, que es **lo que se despliega**, y `latest` como conveniencia de desarrollo.
3. Coolify → recurso lejovista → **Redeploy**: pullea las imágenes, corre `migrate` (`alembic upgrade head`) y reinicia el stack.

El `Redeploy` deja el esquema al día igual que en local — el servicio `migrate` corre una sola vez (no una por réplica) antes de que `backend` levante.

- `IMAGE_PREFIX` / `IMAGE_TAG` son variables del recurso en Coolify. **`IMAGE_TAG` se actualiza al `${sha}` en cada despliegue** (spec 14): `latest` no es un artefacto identificable, y sin identificador el rollback no tiene a qué volver. Volver atrás es repinear `IMAGE_TAG` al sha bueno y redeployar; las imágenes anteriores no se borran.
- Qué commit corre se pregunta, no se deduce: `GET /version` devuelve el sha horneado en la imagen y si sus migraciones están aplicadas.
- El backend **no** publica puertos al host (acceso solo vía el proxy de Coolify).
- Volúmenes: `postgres_data`, `redis_data`, `media_data` (imágenes subidas, compartido entre `backend` y `celery-worker-creation`).

### `build-push.sh` (fallback manual)
Normalmente el build lo hace CI. `build-push.sh` es la alternativa manual desde la Mac si Actions está caído:
- Usa `docker buildx` (cross-platform, `PLATFORM=linux/amd64` por defecto).
- Publica dos imágenes: backend (única) y frontend.
- **Inyecta las `NEXT_PUBLIC_*` en build**: `https://api.<BASE_DOMAIN>`, `wss://api.<BASE_DOMAIN>`. Por eso cambiar de dominio exige reconstruir el frontend.

## 4. Dockerfiles

### Backend (`backend/Dockerfile`)
Multi-stage:
- **builder**: instala build tools + headers de WeasyPrint, crea venv en `/opt/venv`, instala `requirements.txt`.
- **runtime**: `python:3.11-slim` + venv copiado + librerías de sistema de WeasyPrint en runtime (`libpango`, `libcairo`, `fonts-dejavu-core`). ~400 MB. CMD apto para prod (`uvicorn` sin `--reload`).

### Frontend (`frontend/Dockerfile`)
Multi-stage:
- **deps**: `pnpm install --frozen-lockfile`, con la versión de pnpm que declara `packageManager` en `package.json` e instalada por corepack. Node 22, el mismo que CI. Falla si el lockfile no corresponde al manifiesto.
- **builder**: `pnpm run build` con las `NEXT_PUBLIC_*` como ARG → genera `.next/standalone`.
- **runner**: imagen mínima (~250 MB), usuario sin privilegios `nextjs`, copia `public/`, `.next/standalone`, `.next/static`. `CMD ["node", "server.js"]`.

## 5. Nginx (reverse proxy multi-tenant)

### Local (`nginx/conf.d/default.conf`)
- `~^(?<subdomain>.+)\.localhost$` → frontend (inyecta `X-Tenant-Slug: $subdomain`).
- `api.localhost` → backend.
- `localhost` → frontend (landing).

### Producción (Coolify)
No hay archivo de nginx en prod: **Coolify actúa de reverse proxy y termina el TLS** (Let's Encrypt automático) para `lejovista.agenciakibo.com` y `api.lejovista.agenciakibo.com`. Los subdominios/dominios se asignan en la UI de Coolify por servicio.

### Túnel / single-host (`nginx/conf.d-tunnel/tunnel.conf` + `docker-compose.tunnel.yml`)
- Un solo hostname; rutea por **path**: `/ws/` (con upgrade headers), `/api/` → backend; el resto → frontend.
- Pensado para exponer con ngrok/Cloudflare Tunnel sin subdominios comodín. Requiere `NEXT_PUBLIC_SINGLE_HOST_MODE=true` en el build del frontend. Ver `TUNNEL.md`.

## 6. Migraciones en producción

- `AUTO_CREATE_TABLES=false`. El esquema se aplica con `alembic upgrade head`.
- **Automático desde el `up -d` (`O-2`, resuelto)**: el servicio `migrate` (misma imagen que `backend`, `restart: "no"`) corre la migración y termina antes de que `backend` arranque (`depends_on: condition: service_completed_successfully`) — ya no es un paso manual que se pueda olvidar tras un `pull`. Seguro en despliegues multi-réplica: un único contenedor migra, no cada réplica de `backend` compitiendo por aplicarla a la vez.
- Ver la cadena de migraciones en [05-base-de-datos](05-base-de-datos.md).

## 7. Observabilidad en producción

- **Healthchecks** de Docker en todos los servicios críticos (`D-2`).
- `GET /health/deep` para un uptime-monitor externo (detecta pipeline degradado si no hay sync exitoso en 24 h).
- Sentry si `SENTRY_DSN` está configurado.

## 8. CI/CD

- **CI** (`.github/workflows/ci.yml`): en cada push/PR a `main` corre, en la misma ejecución, cinco trabajos — coherencia (paridad de versiones, límites de recursos, migraciones compatibles hacia atrás), backend contra PostgreSQL y Redis reales, backend contra el motor embebido, frontend (lint+tsc+jest+build) y construcción de las dos imágenes sin publicarlas.
- **CD** (`.github/workflows/deploy.yml`): se dispara solo si CI termina en éxito sobre `main` (`workflow_run`). **Solo construye y publica** backend/frontend en GHCR con tags `${sha}` + `latest` (reemplaza el uso rutinario de `build-push.sh`, que queda como alternativa manual/local). **No despliega**: el deploy lo hace Coolify.
- **Deploy**: manual desde el panel de Coolify (recurso lejovista → Redeploy). Automatizable a futuro con un *deploy webhook* de Coolify llamado como paso extra en `deploy.yml`. Flujo completo y rollback en [`docs/infra/deploy-runbook.md`](infra/deploy-runbook.md).
- Variable necesaria en el repo: `BASE_DOMAIN` (Actions variable) — se inlina en las `NEXT_PUBLIC_*` del build del frontend. Los secrets de deploy por SSH (`VPS_*`) ya **no se usan** (se eliminó el job SSH con la migración a Coolify).

## 9. Scripts auxiliares

- `backend/scripts/seed_tenant.py` — siembra org + usuario admin + credencial + cuenta.
- `backend/scripts/setup-hostgator.sh` — script de aprovisionamiento (entorno específico).
