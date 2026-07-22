# Reset del VPS agenciakibo.com + Coolify + n8n + Evolution + lejovista + wacrm (CRM)

## Contexto

El VPS (`vps-1803332.agenciakibo.com`, 69.6.220.199) corre hoy un stack manual con
Traefik + docker compose (`/opt/n8n-traefik/docker-compose.yml`): n8n (main+worker),
Evolution API (WhatsApp), Postgres y Redis compartidos. No hay panel de gestión, no
está Coolify, y el proyecto `lejovista` (este repo) nunca se ha desplegado ahí.

El objetivo es dejar el servidor en limpio, instalar **Coolify** como capa de
gestión/proxy, y recrear/desplegar bajo él **cuatro aplicaciones**, cada una con su
propia URL: n8n, Evolution API, lejovista y **wacrm** (un CRM de WhatsApp nuevo).
**Coolify es el que "maneja" (despliega, enruta, da TLS y monitorea) las cuatro
apps** — wacrm NO es un panel de infraestructura, es una de las apps gestionadas.
No importa preservar datos: ni workflows de n8n, ni la sesión de WhatsApp de
Evolution (se re-escanea el QR después).

**Sobre el SO (Hostgator):** el usuario no puede reinstalar el SO desde su panel, así
que el "reset en limpio" se hace **a nivel de Docker** sobre el AlmaLinux 9 actual
(parar/borrar contenedores, volúmenes, redes e imágenes), no un reimage. Coolify es
100% Docker por debajo, así que funciona sobre AlmaLinux 9 — solo está menos
documentado que sobre Ubuntu/Debian (guías asumen apt, no dnf). Si el instalador
oficial falla por algo específico de RHEL/dnf, se resuelve en el momento.

**Sobre wacrm ([ArnasDon/wacrm](https://github.com/ArnasDon/wacrm)) — matices que
definen su despliegue:**
1. **No es Docker.** Es una app **Next.js 16** para hosting de Node → en Coolify se
   despliega **desde Git con Nixpacks** (no desde una imagen). Puerto interno 3000.
2. **Backend = Supabase** (Postgres + Auth + Storage + RLS). Decisión tomada:
   **Supabase Cloud gestionado**, no self-host. Proyecto ya creado
   (`lzqtsvvqimvwtludewud`); credenciales en `docs/infra/wacrm.env` (gitignored).
3. **Usa la API oficial de WhatsApp (Meta Cloud API), NO Evolution API.** wacrm y
   Evolution son integraciones de WhatsApp distintas; wacrm **no** se conecta al
   Evolution existente por defecto. Integrarlos (Evolution ↔ CRM vía n8n + REST API
   de wacrm) queda como trabajo futuro, fuera del alcance de este plan.

## Esquema de URLs

| Servicio | URL | DNS |
|---|---|---|
| Panel Coolify | `coolify.agenciakibo.com` | ✅ ya creado |
| n8n | `n8n.agenciakibo.com` | ✅ ya creado |
| Evolution API | `api.evolution.agenciakibo.com` | ✅ ya creado |
| lejovista — frontend | `lejovista.agenciakibo.com` | ✅ ya creado |
| lejovista — backend/API | `api.lejovista.agenciakibo.com` | ✅ ya creado |
| **wacrm (CRM)** | **`crm.agenciakibo.com`** | ⬜ **falta crear (nuevo)** |

Supabase Cloud es externo (dominio de supabase.co), no necesita DNS propio.

## Pasos

### 1. Guardar copia del compose actual de Traefik (referencia/aprendizaje)
Antes de borrar nada, copiar `/opt/n8n-traefik/docker-compose.yml` (y
`/docker/n8n/docker-compose.bak`) a `docs/infra/vps-traefik-compose-backup-2026-07-22.yml`.
No incluye datos, solo la definición. Las credenciales que trae quedan obsoletas al
borrar el stack; aun así **gitignored** (ver `.gitignore`), no commitear en claro.

### 2. Backup de la base de datos Postgres (solo referencia, no restauración)
Antes de borrar el volumen `postgres_data`, dump de ambas bases:
```
docker exec postgres pg_dump -U db_user db_n8n > db_n8n_backup_2026-07-22.sql
docker exec postgres pg_dump -U db_user evolution > evolution_backup_2026-07-22.sql
```
Copiar a `docs/infra/backups/` (gitignored). Solo respaldo de consulta; el stack
nuevo arranca vacío.

### 3. Escribir la guía de conexión SSH al VPS
Hecho: [`docs/infra/guia-ssh-vps.md`](guia-ssh-vps.md) — datos de conexión (host,
`root`, puerto `22022`), comando, alias `~/.ssh/config`, migración a llave SSH,
limpieza de host key, y acceso al panel de Coolify. La contraseña **no** va en el
repo (gestor de contraseñas).

### 4. Limpieza total de Docker (por SSH, AlmaLinux 9)
- `docker compose down` en `/opt/n8n-traefik/`, luego `docker rm -f $(docker ps -aq)`.
- Borrar volúmenes (`docker volume rm $(docker volume ls -q)`) y la red custom
  (`docker network rm n8n-traefik_app_network`).
- `docker system prune -a --volumes -f`.
- Eliminar `/opt/n8n-traefik` y `/docker/n8n`.
- SO, IP y SSH (22022) se mantienen — no hay reimage.

### 5. DNS
Los 5 registros existentes ya apuntan a la IP. **Falta un registro A nuevo:**
- ⬜ `crm.agenciakibo.com` → 69.6.220.199 (lo crea el usuario en su panel DNS).

### 6. Hardening básico (por SSH, AlmaLinux 9)
- `dnf update -y`.
- `fail2ban` con jail para sshd (vía EPEL) — el server registró 815 intentos
  fallidos de login por fuerza bruta.
- `firewalld` permitiendo solo 22022, 80, 443 (+ puerto del panel Coolify si aplica).

### 7. Instalar Coolify
- `curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash` (instala Docker si
  falta, levanta su proxy Traefik + panel).
- Dominio del panel `coolify.agenciakibo.com` con SSL automático.
- Crear un **Project** para agrupar las **4 apps**.

### 8. Recrear n8n en Coolify
Base: `/opt/n8n-traefik/docker-compose.yml` (copia guardada):
- Quitar `traefik` (Coolify pone su proxy).
- Mantener `redis`, `postgres`, `n8n-main`, `n8n-worker` (modo `queue`, DB `db_n8n`).
- **Nuevas** credenciales (password Postgres, `N8N_ENCRYPTION_KEY`).
- Dominio `n8n.agenciakibo.com` vía UI de Coolify.

### 9. Recrear Evolution API en Coolify
- `evoapicloud/evolution-api:v2.3.6` con su Postgres+Redis (separados de n8n).
- Nueva `AUTHENTICATION_API_KEY`. Dominio `api.evolution.agenciakibo.com`.
- Tras el deploy: re-escanear el QR de WhatsApp.

### 10. Desplegar lejovista en Coolify
Base: [docker-compose.prod.yml](../../docker-compose.prod.yml).
- **Quitar el servicio `nginx`** (bind directo a 80/443 + certs de Certbot chocan con
  el proxy de Coolify). `frontend` y `backend` como servicios internos; Coolify da
  dominio + TLS.
- Pull de `ghcr.io/lrojascode/proyectolejovista-backend` y `-frontend`.
- `BASE_DOMAIN=lejovista.agenciakibo.com`,
  `API_URL`/`NEXT_PUBLIC_API_URL=https://api.lejovista.agenciakibo.com`.
- Env de prod: `SECRET_KEY`, `ENCRYPTION_KEY` (nuevos, ver
  [.env.example](../../.env.example)), `META_APP_ID`/`META_APP_SECRET`,
  `OPENROUTER_API_KEY`; `MOCK_SERVICES=false`, `ENABLE_API_DOCS=false`.
- Correr `migrate` (`alembic upgrade head`) antes de `backend`.
- Nota (Q1): front y API bajo el mismo registrable domain (`agenciakibo.com`) →
  cookie de sesión *same-site*, el bug de "pide login al refrescar" no aplica en prod.

### 11. Desplegar wacrm (CRM) en Coolify
1. **Supabase Cloud:** proyecto ya creado (`lzqtsvvqimvwtludewud`). Falta correr las
   migraciones SQL del repo de wacrm (esquema + RLS) sobre ese proyecto.
2. **App en Coolify:** recurso *Application* apuntando a un **fork propio** de
   `ArnasDon/wacrm`. Build pack **Nixpacks** (Next.js, puerto 3000).
3. **Env vars:** ya preparadas en [`docs/infra/wacrm.env`](wacrm.env) (gitignored) —
   Supabase (URL, anon, service_role) y `ENCRYPTION_KEY` (64 hex) rellenos;
   `NEXT_PUBLIC_SITE_URL=https://crm.agenciakibo.com`, locale `es`. **Pendiente del
   usuario:** `META_APP_SECRET` y `META_APP_ID` (pegar al desplegar; mientras tanto
   `WHATSAPP_TEMPLATES_DRY_RUN=true`). Pegar estas vars en la UI de env de Coolify.
   🔒 El `service_role key` es admin total de Supabase — nunca commitear.
4. **Dominio** `crm.agenciakibo.com` con SSL de Coolify. HTTPS **obligatorio** para
   los webhooks de WhatsApp de Meta.
5. Configurar el webhook de Meta a `https://crm.agenciakibo.com/...` cuando el
   usuario tenga las credenciales de Meta.

### 12. Verificación
- URLs con certificado válido: `coolify.`, `n8n.`, `api.evolution.`, `lejovista.`,
  `api.lejovista.`, **`crm.`**.
- n8n: crear y ejecutar un workflow de prueba.
- Evolution: QR escaneado, instancia conectada, mensaje de prueba enviado.
- lejovista: health del backend (`GET /` = 200), frontend contra
  `api.lejovista.agenciakibo.com`; login + F5 (no debe expulsar).
- wacrm: login al CRM, Supabase OK, webhook de WhatsApp verificado por Meta, mensaje
  entrante en la bandeja.
- Guía SSH: seguir [`guia-ssh-vps.md`](guia-ssh-vps.md) desde cero y confirmar acceso.

## Estado / dependencias
- ✅ DNS: 5 registros hechos; falta `crm.agenciakibo.com`.
- ✅ Supabase Cloud: proyecto y credenciales provistos; `.env` de wacrm preparado
  (`docs/infra/wacrm.env`, gitignored).
- ✅ Guía SSH escrita (`docs/infra/guia-ssh-vps.md`).
- ⚠️ Pendiente del usuario: credenciales de **Meta WhatsApp Cloud API** (wacrm) y el
  registro DNS `crm.agenciakibo.com`.
- ⏳ Ejecución en el VPS (pasos 1–2, 4, 6–12): pendiente del visto bueno explícito —
  es destructiva sobre el stack de producción actual (n8n/Evolution).

## Notas
- Se guardan backups de **referencia** (compose Traefik + dumps SQL), no se restauran.
- No hay reimage de SO: AlmaLinux 9, SSH 22022, misma IP. Única incertidumbre: que el
  instalador de Coolify choque con algo de RHEL/dnf (se ajusta al vuelo).
