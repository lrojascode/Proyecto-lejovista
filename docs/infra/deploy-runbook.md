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
   - `:<sha>` — inmutable. **Es lo que se despliega**, y a lo que se vuelve.
   - `:latest` — conveniencia de desarrollo. Ya no se despliega con él.
5. **Deploy** (manual): Coolify → proyecto → recurso **lejovista** →
   Environment Variables → `IMAGE_TAG=<sha>` → **Redeploy**. Coolify:
   - hace `pull` de las imágenes de ese sha desde GHCR,
   - corre el servicio `migrate` (`alembic upgrade head`) una sola vez,
   - reinicia backend + workers + beat + frontend.
6. **Verificar**, que es parte del despliegue y no un extra:

   ```bash
   COMMIT_ESPERADO=<sha> SMOKE_EMAIL=... SMOKE_PASSWORD=... \
     scripts/despliegue/smoke.sh https://api.lejovista.agenciakibo.com
   ```

Esperá a que el deploy quede *healthy* en Coolify y a que el smoke test dé
`DESPLIEGUE VERIFICADO`. Deja su resultado fechado en
`docs/infra/operacion/despliegues/`.

## Por qué el sha y no `latest`

`latest` no es un artefacto: es "lo último que se construyó". Con él, saber qué
está corriendo en producción es una deducción —mirar la hora del último build y
suponer que el pull la trajo— en vez de un dato.

Y sin ese dato el rollback no tiene a qué volver. Desplegando por sha, la
pregunta "¿qué hay desplegado?" se responde con una llamada:

```bash
curl -s https://api.lejovista.agenciakibo.com/version
```

que devuelve el commit horneado en la imagen y, además, si las migraciones que
esa imagen trae están aplicadas. Ese segundo dato atrapa el fallo más caro y más
silencioso de un despliegue: que el contenedor `migrate` no corriera. La app
arranca igual, responde igual, y va rompiéndose por donde toca el esquema nuevo.

### Requisito de configuración

En el recurso lejovista de Coolify → **Environment Variables**:

- `IMAGE_PREFIX=ghcr.io/lrojascode` (una vez).
- `IMAGE_TAG=<sha>` — **se actualiza en cada despliegue**. Es el paso que
  convierte "desplegar" en "desplegar algo identificable".
- "Force pull" deja de ser imprescindible al desplegar por sha: un tag inmutable
  no cambia de contenido, así que la caché no puede servir algo distinto. Con
  `latest` sí hacía falta, y **sin él un Redeploy podía no traer el código
  nuevo** — otra razón para no volver a la etiqueta flotante.
- GHCR debe ser accesible por Coolify (registry privado conectado, o paquetes
  públicos).

---

## ⚠️ Cambiar un `:-default` del compose NO cambia el valor en Coolify

`ENABLE_META_WRITEBACK: '${ENABLE_META_WRITEBACK:-true}'` **no asigna `true`**:
es el valor a usar *si la variable no existe o está vacía*.

Y Coolify **congela el valor la primera vez que parsea el compose**. Si en ese
momento el default era `false`, guarda `false` y lo conserva. Después muestra la
fila como *"Managed by Docker Compose — Update this value in the Compose file"*,
de solo lectura... pero editar el compose **ya no actualiza el valor guardado**.
Queda un estado donde el compose dice `:-true`, el panel no se deja editar, y el
contenedor recibe `false`. Redesplegar no cambia nada: el valor de origen es el
mismo.

**Remedio:** poner el literal en el compose, sin interpolación, para que no haya
variable que administrar:

```yaml
ENABLE_META_WRITEBACK: 'true'
```

Si Coolify igual inyecta el valor viejo, renombrar la clave (una que nunca haya
visto) fuerza una entrada nueva con el default correcto.

Costó horas de debugging el 14-ago-2026: campañas que decían "creada" y no
existían en Meta, porque el write-back estaba apagado con un compose que
aparentaba tenerlo encendido.

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
**cualquier** versión pasada. Las imágenes anteriores no se borran: son el camino
de vuelta, y por eso este repositorio no tiene ninguna política de limpieza de
paquetes en GHCR.

1. Coolify → recurso lejovista → **Environment Variables** → `IMAGE_TAG` = el
   `<sha>` de la versión buena (aparece en el resumen de la corrida de Actions y
   en los tags de los paquetes GHCR).
2. **Redeploy**.
3. Verificar que volvió la que se quería:

   ```bash
   COMMIT_ESPERADO=<sha bueno> SMOKE_EMAIL=... SMOKE_PASSWORD=... \
     scripts/despliegue/smoke.sh https://api.lejovista.agenciakibo.com
   ```

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

## Migraciones de base de datos

El rollback de **imagen** NO revierte migraciones ya aplicadas, y no hace falta
que lo haga: la política es que toda migración deje funcionando a la imagen
anterior. Se añade, se escribe en paralelo, se conmuta la lectura, y la retirada
de lo viejo va en un despliegue posterior.

Eso no es una recomendación: `verificar_migraciones_compatibles.py` corre en CI y
falla la construcción si una migración borra una columna o una tabla sin
declararlo. El procedimiento completo, y qué hacer con los cambios que de verdad
no admiten la escalera —ventana de mantenimiento, modo de solo lectura, respaldo
externo comprobado antes— está en
[`operacion/migraciones.md`](operacion/migraciones.md).

---

## Fallback: build manual (si GitHub Actions está caído)

Desde la Mac, con login previo a GHCR
(`echo $TOKEN | docker login ghcr.io -u lrojascode --password-stdin`):

```bash
BASE_DOMAIN=lejovista.agenciakibo.com ./build-push.sh
```

Publica las imágenes `:latest` en GHCR; luego Redeploy en Coolify como siempre.
