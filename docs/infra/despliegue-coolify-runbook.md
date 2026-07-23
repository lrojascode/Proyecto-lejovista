# Runbook — Despliegue en Coolify (registro de lo ejecutado)

Registro detallado de la migración del VPS `agenciakibo.com` a Coolify, ejecutada
el 2026-07-22/23. Complementa el plan ([plan-migracion-coolify.md](plan-migracion-coolify.md))
con **lo que realmente se hizo**, los problemas encontrados y cómo se resolvieron.

## Arquitectura final

VPS `69.6.220.199` (AlmaLinux 9, SSH puerto **22022**) → **Coolify v4.1.2** gestiona
4 apps, todas con TLS automático (Let's Encrypt vía el proxy de Coolify):

| App | URL | Tipo de recurso en Coolify | Puerto |
|---|---|---|---|
| Panel Coolify | `coolify.agenciakibo.com` | (el propio Coolify) | 8000→443 |
| n8n (main+worker, queue) | `n8n.agenciakibo.com` | Docker Compose | 5678 |
| Evolution API | `api.evolution.agenciakibo.com` | Docker Compose | 8080 |
| lejovista frontend | `lejovista.agenciakibo.com` | Docker Compose (imágenes GHCR) | 3000 |
| lejovista backend/API | `api.lejovista.agenciakibo.com` | (mismo recurso) | 8000 |
| wacrm (CRM) | `crm.agenciakibo.com` | Public Repository (Nixpacks) | 3000 |

- Coolify server: **"This Machine" (localhost)**, SSH puerto **22022** (no el 22 por
  defecto — ese fue el "Server is not reachable" inicial).
- n8n y Evolution tienen **Postgres + Redis dedicados** (aislados entre sí).
- wacrm usa **Supabase Cloud** (proyecto `lzqtsvvqimvwtludewud`), externo al VPS.

## Lo ejecutado en el VPS (por SSH)

1. **Backups de referencia** (antes de destruir): compose de Traefik + `pg_dump` de
   `db_n8n` (221MB) y `evolution` (10MB) → traídos a `docs/infra/backups/`
   (gitignored, integridad verificada).
2. **Limpieza Docker**: `docker compose down` del stack viejo + `docker rm -f`,
   `docker volume rm`, `docker network rm`, `docker system prune -a --volumes`
   (3GB liberados). Borradas `/opt/n8n-traefik` y `/docker/n8n`. SSH intacto (sshd
   es servicio del host, no Docker).
3. **Hardening**: `fail2ban` (jail sshd puerto 22022, vía EPEL) — el server tenía
   815 intentos de fuerza bruta. **firewalld DIFERIDO** a propósito (riesgo de
   lockout + conflicto de iptables con Docker/Coolify) — ver mejoras futuras.
4. **Coolify**: instalado con el script oficial. Panel en `http://IP:8000` → el
   usuario creó la cuenta admin (el primer registro es admin; se hizo de inmediato
   porque el panel queda expuesto).

## Detalle por app

Todos los composes adaptados para Coolify (sin Traefik/labels — Coolify pone el
proxy) viven en [`docs/infra/coolify/`](coolify/). Los secretos van en la UI de
Environment Variables de Coolify (los `.env` de referencia están gitignored).

- **n8n** — [n8n.compose.yml](coolify/n8n.compose.yml). Modo `queue`, credenciales
  nuevas (`docs/infra/coolify/n8n.env`, gitignored). Dominio en el servicio
  `n8n-main`.
- **Evolution** — [evolution.compose.yml](coolify/evolution.compose.yml). Nueva
  `AUTHENTICATION_API_KEY`, `CHATWOOT_ENABLED=false`. La sesión de WhatsApp anterior
  se perdió → re-escanear QR.
- **lejovista** — [lejovista.compose.yml](coolify/lejovista.compose.yml). Imágenes
  de GHCR (`ghcr.io/lrojascode/proyectolejovista-{backend,frontend}:latest`, hechas
  **públicas** para el pull). Sin el servicio `nginx` (lo pone Coolify).
- **wacrm** — Public Repository `github.com/ArnasDon/wacrm` + Nixpacks. Env en
  [wacrm.env](coolify/../wacrm.env) (gitignored). Esquema de Supabase aplicado desde
  [wacrm-supabase-schema.sql](wacrm-supabase-schema.sql) (36 migraciones concatenadas,
  pegadas en el SQL Editor de Supabase; idempotentes).

## Problemas encontrados y cómo se resolvieron (para futuros deploys)

1. **Coolify "Server is not reachable"** → el VPS tiene SSH en **22022**, no 22.
   Fix: poner puerto 22022 en el server de Coolify.
2. **Imágenes de lejovista las construye GitHub Actions**, no la Mac
   ([deploy.yml](../../.github/workflows/deploy.yml)) — no hace falta `docker login`
   ni `build-push.sh` local.
3. **Frontend con `localhost` incrustado** → las `NEXT_PUBLIC_*` se **inlinean en
   build-time**. La CI las toma de la **variable de repositorio** `BASE_DOMAIN`.
   Gotcha: debe ser **Repository variable**, NO Environment variable — el job
   `build-and-push` no declara `environment:`, así que no ve las de environment.
   Fijarla y **re-ejecutar** el workflow (fijar la variable no reconstruye sola).
4. **migrate exit 1 — `ENABLE_API_DOCS` vacío** → pydantic no parsea `''` como bool.
   Fix: defaults en el compose (`${VAR:-false}`) para todos los booleanos.
5. **migrate exit 1 — password auth failed** → el volumen de Postgres se inicializó
   con una contraseña y luego `DB_PASSWORD` cambió/quedó vacía. Fixes:
   (a) el compose **construye** `DATABASE_URL`/`REDIS_URL` desde `DB_PASSWORD`/
   `REDIS_PASSWORD` (nunca se desincronizan); (b) al no haber datos, se **borró el
   volumen** `..._postgres-data` para re-inicializar limpio.
6. **Registro "Failed to fetch" (lejovista)** → CORS: la regex de
   `backend/app/main.py` estaba hardcodeada a `localhost`/`lrojas.dev`. Fixes:
   (a) inmediato, env `TUNNEL_ORIGIN_REGEX` en Coolify; (b) permanente, la regex
   ahora **deriva de `BASE_DOMAIN`** (aplica en el próximo build del backend).
7. **Coolify duplica los backslashes de una env** → `\.` quedó como `\\.` y la regex
   no matcheaba. Fix: usar `[.]` en vez de `\.` en el valor de `TUNNEL_ORIGIN_REGEX`.

## Mapa de credenciales (dónde vive cada secreto)

- **Coolify env UI** (por recurso): la fuente de verdad en runtime de cada app.
- **`docs/infra/coolify/*.env` y `docs/infra/wacrm.env`** (gitignored): copias de
  referencia con las credenciales **nuevas** generadas para prod.
- **`docs/infra/backups/`** (gitignored): dumps + composes viejos + `.env` de
  Coolify (recomendado respaldar `/data/coolify/source/.env` aparte).
- **Supabase Dashboard**: keys de wacrm (anon, service_role).
- **Contraseña SSH del VPS**: gestor de contraseñas (NO en el repo). Ver
  [guia-ssh-vps.md](guia-ssh-vps.md).

## Operación

- **Redeploy de una app**: Coolify → recurso → Deploy (hace pull de imágenes /
  rebuild de Nixpacks).
- **Nuevo dominio**: servicio → Settings → Domains.
- **SSH**: `ssh -p 22022 root@69.6.220.199` (ver [guia-ssh-vps.md](guia-ssh-vps.md)).
- **CI**: push a `main` → CI (tests) → Deploy (build & push de imágenes a GHCR).
  Tras el push, redeploy en Coolify para bajar la imagen nueva.

## Pendientes / mejoras futuras

Ver [../mejoras-futuras.md](../mejoras-futuras.md): app de Meta por cliente y
multi-tenant por subdominio wildcard. Además:
- Onboarding: registrar cuentas (lejovista, wacrm), re-escanear QR (Evolution),
  conectar Meta a lejovista, cargar Meta Cloud API a wacrm (`WHATSAPP_TEMPLATES_DRY_RUN=false`).
- `firewalld` (diferido) con red de seguridad anti-lockout.
- Auto-deploy: webhook de Coolify disparado por la CI (hoy el redeploy es manual).
