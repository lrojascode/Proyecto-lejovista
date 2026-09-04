#!/usr/bin/env bash
# Base compartida del sistema de respaldo (spec 04).
#
# El respaldo anterior escribia y leia EN EL MISMO VPS que protege: un fallo de
# disco, un borrado del volumen o la perdida del servidor se llevaba por delante
# la base y sus copias a la vez. Tampoco estaba cifrado, ni cubria la
# configuracion ni los medios.
#
# Tres decisiones que gobiernan todo lo demas:
#
# 1. **El respaldo de referencia es la copia REMOTA.** La local se conserva como
#    camino rapido, pero el criterio de exito del proceso diario es que la copia
#    remota exista y sea legible, no que el volcado local se haya escrito.
#
# 2. **Cifrado asimetrico, en origen.** El VPS solo tiene la clave PUBLICA: puede
#    cifrar y no puede descifrar. La privada se custodia fuera. Con cifrado
#    simetrico, la clave tendria que vivir en el servidor y un atacante que llega
#    al VPS se lleva tambien la forma de leer todos los respaldos.
#
# 3. **El destino remoto es un driver.** `local` (un volumen externo montado, y
#    lo que usan los drills), `rclone` (S3/B2/Drive en produccion) y `ssh`. La
#    interfaz son cuatro operaciones; cambiar de proveedor no toca la logica.

set -euo pipefail

# --- Configuracion -----------------------------------------------------------

LV_BACKUP_WORKDIR="${LV_BACKUP_WORKDIR:-/var/backups/lejovista/work}"
LV_BACKUP_PUBKEY="${LV_BACKUP_PUBKEY:-/etc/lejovista/backup-pubkey.asc}"

# local | rclone | ssh
LV_BACKUP_REMOTE_DRIVER="${LV_BACKUP_REMOTE_DRIVER:-local}"
LV_BACKUP_REMOTE_PATH="${LV_BACKUP_REMOTE_PATH:-}"

# Perdida maxima objetivo (RPO). Un respaldo remoto mas viejo que esto es una
# alerta, no una nota.
LV_RPO_HOURS="${LV_RPO_HOURS:-24}"

# Cuanto se conserva en el remoto.
LV_BACKUP_RETENTION_DAYS="${LV_BACKUP_RETENTION_DAYS:-35}"

# Interruptor de hombre muerto: un servicio externo al VPS que espera este ping.
# Si no llega, ALERTA. Es la unica forma de enterarse de que el host esta caido:
# un sistema que solo alerta desde adentro no puede informar de su propia muerte.
LV_BACKUP_HEARTBEAT_URL="${LV_BACKUP_HEARTBEAT_URL:-}"

# Base de datos. Con contenedor se usa `docker exec`; sin el, las herramientas
# locales de PostgreSQL.
LV_DB_CONTAINER="${LV_DB_CONTAINER:-}"
LV_PGHOST="${LV_PGHOST:-127.0.0.1}"
LV_PGPORT="${LV_PGPORT:-5432}"
LV_PGUSER="${LV_PGUSER:-lejovista}"
LV_PGDATABASE="${LV_PGDATABASE:-lejovista}"

# Los otros dos conjuntos que cubre el respaldo.
LV_MEDIA_DIR="${LV_MEDIA_DIR:-}"
LV_CONFIG_FILES="${LV_CONFIG_FILES:-}"

LV_BACKUP_PREFIX="${LV_BACKUP_PREFIX:-lejovista}"

# --- Registro ----------------------------------------------------------------

log()  { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

# --- Preflight ---------------------------------------------------------------

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "falta la herramienta '$1'. $2"
}

preflight_comun() {
    require_cmd gpg "Instalar gnupg: es lo que cifra el respaldo."
    require_cmd tar "Parte del sistema base."
    [ -f "$LV_BACKUP_PUBKEY" ] \
        || die "no existe la clave publica de respaldo en $LV_BACKUP_PUBKEY. Ver docs/infra/backups/README.md"
    [ -n "$LV_BACKUP_REMOTE_PATH" ] \
        || die "LV_BACKUP_REMOTE_PATH esta vacio: no hay destino remoto y un respaldo que solo vive en el VPS no es un respaldo."

    case "$LV_BACKUP_REMOTE_DRIVER" in
        local) : ;;
        rclone) require_cmd rclone "Instalar rclone y configurar el remoto." ;;
        ssh)   require_cmd ssh "Parte del sistema base."; require_cmd rsync "Instalar rsync." ;;
        *) die "LV_BACKUP_REMOTE_DRIVER='$LV_BACKUP_REMOTE_DRIVER' no es un driver conocido (local|rclone|ssh)." ;;
    esac

    # Que la clave PRIVADA no este en el VPS. Si estuviera, el cifrado no
    # protegeria del escenario que lo motiva: quien entra al servidor se lleva
    # los respaldos Y la forma de leerlos.
    local fpr
    fpr="$(gpg --show-keys --with-colons "$LV_BACKUP_PUBKEY" 2>/dev/null | awk -F: '/^fpr:/{print $10; exit}')"
    [ -n "$fpr" ] || die "la clave publica en $LV_BACKUP_PUBKEY no es legible."
    if gpg --list-secret-keys --with-colons "$fpr" >/dev/null 2>&1; then
        if [ "${LV_ALLOW_SECRET_KEY_PRESENT:-0}" != "1" ]; then
            die "la clave PRIVADA de respaldo esta presente en este host. Debe custodiarse fuera del VPS (ver docs/infra/backups/README.md). Para un drill, exportar LV_ALLOW_SECRET_KEY_PRESENT=1."
        fi
        log "AVISO: la clave privada esta en este host (permitido por LV_ALLOW_SECRET_KEY_PRESENT=1; solo para drills)."
    fi
    echo "$fpr"
}

# Todos los keyid que pertenecen a la clave de respaldo: el primario y sus
# subclaves. En OpenPGP la clave primaria FIRMA y una subclave CIFRA, asi que el
# paquete de un artefacto cifrado lleva el keyid de la SUBCLAVE. Comparar solo
# contra el primario no encuentra nunca la coincidencia — lo detecto el primer
# drill.
keyids_de_la_clave() {
    gpg --show-keys --with-colons "$LV_BACKUP_PUBKEY" 2>/dev/null \
        | awk -F: '/^(pub|sub):/ {print tolower($5)}'
}

# --- Driver del destino remoto ----------------------------------------------
#
# Cuatro operaciones. Cambiar de proveedor es cambiar este bloque, no la logica.

remote_put() {   # remote_put <archivo_local> <nombre_remoto>
    local origen="$1" nombre="$2"
    case "$LV_BACKUP_REMOTE_DRIVER" in
        local)
            mkdir -p "$LV_BACKUP_REMOTE_PATH"
            cp "$origen" "$LV_BACKUP_REMOTE_PATH/$nombre.tmp"
            # Rename atomico: un archivo a medio copiar no puede parecer un
            # respaldo completo ante el verificador.
            mv "$LV_BACKUP_REMOTE_PATH/$nombre.tmp" "$LV_BACKUP_REMOTE_PATH/$nombre"
            ;;
        rclone) rclone copyto --no-traverse "$origen" "$LV_BACKUP_REMOTE_PATH/$nombre" ;;
        ssh)
            local host="${LV_BACKUP_REMOTE_PATH%%:*}" ruta="${LV_BACKUP_REMOTE_PATH#*:}"
            ssh "$host" "mkdir -p '$ruta'"
            rsync -q "$origen" "$host:$ruta/$nombre"
            ;;
    esac
}

remote_list() {  # nombres, uno por linea, mas nuevos primero
    case "$LV_BACKUP_REMOTE_DRIVER" in
        local)
            [ -d "$LV_BACKUP_REMOTE_PATH" ] || return 0
            ls -t "$LV_BACKUP_REMOTE_PATH" 2>/dev/null | grep -E "^${LV_BACKUP_PREFIX}_.*\.tar\.gpg$" || true
            ;;
        rclone)
            rclone lsf "$LV_BACKUP_REMOTE_PATH" 2>/dev/null \
                | grep -E "^${LV_BACKUP_PREFIX}_.*\.tar\.gpg$" | sort -r || true
            ;;
        ssh)
            local host="${LV_BACKUP_REMOTE_PATH%%:*}" ruta="${LV_BACKUP_REMOTE_PATH#*:}"
            ssh "$host" "ls -t '$ruta' 2>/dev/null" \
                | grep -E "^${LV_BACKUP_PREFIX}_.*\.tar\.gpg$" || true
            ;;
    esac
}

remote_get() {   # remote_get <nombre_remoto> <destino_local>
    local nombre="$1" destino="$2"
    case "$LV_BACKUP_REMOTE_DRIVER" in
        local)  cp "$LV_BACKUP_REMOTE_PATH/$nombre" "$destino" ;;
        rclone) rclone copyto "$LV_BACKUP_REMOTE_PATH/$nombre" "$destino" ;;
        ssh)
            local host="${LV_BACKUP_REMOTE_PATH%%:*}" ruta="${LV_BACKUP_REMOTE_PATH#*:}"
            rsync -q "$host:$ruta/$nombre" "$destino"
            ;;
    esac
}

remote_delete() { # remote_delete <nombre_remoto>
    local nombre="$1"
    case "$LV_BACKUP_REMOTE_DRIVER" in
        local)  rm -f "$LV_BACKUP_REMOTE_PATH/$nombre" ;;
        rclone) rclone deletefile "$LV_BACKUP_REMOTE_PATH/$nombre" ;;
        ssh)
            local host="${LV_BACKUP_REMOTE_PATH%%:*}" ruta="${LV_BACKUP_REMOTE_PATH#*:}"
            ssh "$host" "rm -f '$ruta/$nombre'"
            ;;
    esac
}

# --- Utilidades --------------------------------------------------------------

# Epoch (UTC) codificado en el nombre del respaldo. Se lee del NOMBRE y no de la
# fecha del archivo remoto: los proveedores de objetos reescriben mtime al
# copiar, y entonces un respaldo viejo parece nuevo — que es exactamente el
# fallo que el verificador tiene que detectar.
epoch_de_nombre() {
    local nombre="$1" sello
    sello="$(printf '%s' "$nombre" | sed -E "s/^${LV_BACKUP_PREFIX}_([0-9]{8}T[0-9]{6}Z)_.*/\1/")"
    [ "$sello" != "$nombre" ] || return 1
    local iso="${sello:0:4}-${sello:4:2}-${sello:6:2}T${sello:9:2}:${sello:11:2}:${sello:13:2}Z"
    if date -u -d "$iso" +%s 2>/dev/null; then return 0; fi          # GNU
    date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null        # BSD/macOS
}

# Suma de verificacion del artefacto. `sha256sum` en Linux, `shasum -a 256` en
# macOS: el drill se corre en las dos partes.
sha256_de() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

psql_exec() {    # ejecuta pg_dump/psql segun haya contenedor o no
    if [ -n "$LV_DB_CONTAINER" ]; then
        docker exec -i "$LV_DB_CONTAINER" "$@"
    else
        "$@"
    fi
}
