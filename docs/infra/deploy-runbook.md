# Runbook de deploy — lejovista en Coolify

Cómo llevar cambios de local a producción y cómo volver atrás si algo sale mal.

lejovista corre en producción como recurso **Docker Compose de Coolify** sobre el
VPS, tirando de imágenes desde **GHCR**. Coolify pone el proxy + TLS. Dominios:

- Frontend: `https://lejovista.agenciakibo.com`
- Backend/API: `https://api.lejovista.agenciakibo.com`
- Panel Coolify: `https://coolify.agenciakibo.com`

El stack de prod está en [`coolify/lejovista.compose.yml`](coolify/lejovista.compose.yml)
(idéntico al local pero **sin `nginx`**).

---

## Deploy (flujo normal)

1. **Local**: trabajás con `docker-compose.yml` (hot-reload) o `backend/dev-run-local.sh`.
2. **Publicar**: commit + `git push` a `main`.
3. **CI** (`.github/workflows/ci.yml`) corre los tests. Si fallan, nada más ocurre.
4. **Build** (`.github/workflows/deploy.yml`, tras CI verde): construye y publica las
   imágenes en GHCR con dos tags:
   - `:latest` — lo que Coolify pullea en un Redeploy normal.
   - `:<sha>` — inmutable, para rollback determinista.
5. **Deploy** (manual): entrás a Coolify → proyecto → recurso **lejovista** →
   botón **Redeploy**. Coolify:
   - hace `pull` de las imágenes `:latest` desde GHCR,
   - corre el servicio `migrate` (`alembic upgrade head`) una sola vez,
   - reinicia backend + workers + beat + frontend.

Esperá a que el deploy quede *healthy* en Coolify y verificá el cambio en
`https://lejovista.agenciakibo.com`.

### Requisito de configuración (una sola vez)

En el recurso lejovista de Coolify → **Environment Variables**:

- `IMAGE_TAG=latest` (y `IMAGE_PREFIX=ghcr.io/lrojascode`).
- Activar **"Force pull"** (o equivalente) para que cada Redeploy re-descargue la
  imagen `:latest` de GHCR y no reuse la cacheada. **Sin esto, un Redeploy puede no
  traer el código nuevo.**
- GHCR debe ser accesible por Coolify (registry privado conectado, o paquetes
  públicos).

---

## ⚠️ El `:-default` del compose NO pisa a la variable del panel

En el compose, `ENABLE_META_WRITEBACK: '${ENABLE_META_WRITEBACK:-true}'` **no
asigna `true`**: dice *"usá la variable; si no existe o está vacía, entonces
`true`"*. Si en **Environment Variables** de Coolify hay una fila
`ENABLE_META_WRITEBACK=false`, esa gana y el default no se aplica nunca.

Editar el compose no arregla un valor equivocado del panel, y **reiniciar o
redesplegar tampoco**: el valor de origen sigue siendo el mismo. Hay que
corregir (o borrar) la fila en el panel.

Costó horas de debugging el 14-ago-2026: campañas que decían "creada" y no
existían en Meta, porque el write-back estaba apagado desde el panel mientras
el compose parecía tenerlo encendido.

**Cómo ver el valor efectivo**, que es la única fuente de verdad — terminal del
servicio que importa (para creación de campañas, `celery-worker-creation`, no
el backend):

```bash
printenv | grep -E "MOCK_SERVICES|ENABLE_META_WRITEBACK|WRITEBACK_ACCOUNT"
```

El backend además lo dice al arrancar, en un `WARNING` de la primera línea:
`ESCRITURAS A META SIMULADAS (ENABLE_META_WRITEBACK=false): ...` o
`Escrituras a Meta REALES para TODAS las cuentas`.

---

## Rollback (volver a una versión anterior)

De más rápido a más robusto:

### 1. Repin de `IMAGE_TAG` (recomendado, determinista)

Como cada build queda publicado en GHCR con su `:<sha>` inmutable, podés volver a
**cualquier** versión pasada:

1. Coolify → recurso lejovista → **Environment Variables** → `IMAGE_TAG` = el
   `<sha>` de la versión buena (el SHA del commit; aparece en la corrida de Actions
   y en los tags de los paquetes GHCR).
2. **Redeploy**.
3. Para volver a la normalidad más adelante: `IMAGE_TAG=latest` → Redeploy.

Funciona siempre, aunque la caché local del VPS se haya podado, porque la imagen
vieja sigue viva en GHCR.

### 2. Botón "Rollback" de Coolify (rápido, pero limitado)

Coolify guarda deployments previos y ofrece "Rollback" en la UI, **pero solo si la
imagen anterior sigue cacheada localmente en el VPS** (limitación oficial). Sirve
para deshacer justo después de un deploy malo; si ya se podó la caché, usá la
opción 1.

### 3. Roll-forward por git (el más limpio a largo plazo)

`git revert <commit>` en `main` → CI reconstruye → Redeploy en Coolify. Deja la
historia coherente (producción siempre refleja `main`).

---

## ⚠️ Caveat: migraciones de base de datos

El rollback de **imagen** NO revierte migraciones de alembic ya aplicadas. Regla
operativa:

- Mantener migraciones **forward-compatible** (aditivas): agregar columnas/tablas
  nullable, no borrar ni renombrar en el mismo deploy que el código que lo usa.
- Si un deploy incluye una migración destructiva y hay que revertir el código, puede
  hacer falta un `alembic downgrade` manual dentro del contenedor antes de que el
  código viejo funcione. Evaluá caso por caso.

---

## Fallback: build manual (si GitHub Actions está caído)

Desde la Mac, con login previo a GHCR
(`echo $TOKEN | docker login ghcr.io -u lrojascode --password-stdin`):

```bash
BASE_DOMAIN=lejovista.agenciakibo.com ./build-push.sh
```

Publica las imágenes `:latest` en GHCR; luego Redeploy en Coolify como siempre.
