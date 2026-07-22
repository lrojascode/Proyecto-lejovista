# 14 · Deployment

## 1. Topología de contenedores

Una sola imagen de **backend** se reutiliza para el API, los 3 workers y beat (solo cambia el `command`). El **frontend** es una imagen Next.js standalone. Nginx es el único punto de entrada.

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
| `nginx` | `nginx:alpine` | Reverse proxy + TLS |

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

## 3. Producción (`docker-compose.prod.yml`)

Filosofía: **no se construye nada en el VPS**. Las imágenes se compilan en la máquina del desarrollador y se publican en un registry (GHCR por defecto); el VPS solo hace `pull`.

```bash
# En la máquina de build (ej. Mac arm64 → target amd64):
BASE_DOMAIN=tudominio.com ./build-push.sh

# En el VPS:
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker image prune -f
```

Igual que en local, el `pull`+`up -d` ya deja el esquema al día — el servicio `migrate` corre `alembic upgrade head` una sola vez (no una por réplica) antes de que `backend` levante.

- `IMAGE_PREFIX` / `IMAGE_TAG` se leen del `.env` (default `ghcr.io/lrojascode` / `latest`).
- El backend **no** publica puertos al host (acceso solo vía Nginx).
- Volúmenes: `postgres_data`, `redis_data`, `media_data` (imágenes subidas, compartido entre `backend` y `celery-worker-creation`).
- Nginx monta `production.conf.example` como `default.conf` y los certificados de `/etc/letsencrypt`.

### `build-push.sh`
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
- **deps**: `npm ci`.
- **builder**: `npm run build` con las `NEXT_PUBLIC_*` como ARG → genera `.next/standalone`.
- **runner**: imagen mínima (~250 MB), usuario sin privilegios `nextjs`, copia `public/`, `.next/standalone`, `.next/static`. `CMD ["node", "server.js"]`.

## 5. Nginx (reverse proxy multi-tenant)

### Local (`nginx/conf.d/default.conf`)
- `~^(?<subdomain>.+)\.localhost$` → frontend (inyecta `X-Tenant-Slug: $subdomain`).
- `api.localhost` → backend.
- `localhost` → frontend (landing).

### Producción (`nginx/conf.d/production.conf.example`)
Plantilla con TLS y subdominios reales (a completar por dominio).

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

- **CI** (`.github/workflows/ci.yml`): en cada push/PR a `main` corre pytest+alembic (backend) y tsc+jest+build (frontend).
- **CD** (`.github/workflows/deploy.yml`): se dispara solo si CI termina en éxito sobre `main` (`workflow_run`). Construye y publica backend/frontend en GHCR (reemplaza el uso rutinario de `build-push.sh`, que queda como alternativa manual/local) y luego un job de deploy hace SSH al VPS (`pull` + `up -d`).
- **Gate manual**: el job de deploy usa el Environment `production`, configurado en GitHub (Settings → Environments) con *required reviewers* — queda en pausa hasta que alguien lo aprueba desde la pestaña Actions. Mientras el proyecto siga cambiando rápido, cada despliegue real requiere ese clic explícito.
- Secrets/vars necesarios en el repo: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (secrets) y `BASE_DOMAIN`, `VPS_DEPLOY_PATH` (variables).

## 9. Scripts auxiliares

- `backend/scripts/seed_tenant.py` — siembra org + usuario admin + credencial + cuenta.
- `backend/scripts/setup-hostgator.sh` — script de aprovisionamiento (entorno específico).
