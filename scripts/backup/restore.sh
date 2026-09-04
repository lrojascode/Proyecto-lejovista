#!/usr/bin/env bash
# Restauracion completa desde el respaldo remoto (spec 04, criterios 7-9).
#
# Parte de un HOST LIMPIO y solo de dos cosas: el respaldo remoto y la clave
# privada custodiada. Ningun artefacto del servidor original. Es la unica forma
# de descubrir que falta — un backup que nunca se restauro es una hipotesis.
#
#   ./scripts/backup/restore.sh --destino lejovista_restaurada [--respaldo <nombre>]
#
# Requiere la clave PRIVADA en el llavero de gpg. Deliberadamente NO se
# automatiza su instalacion: es la credencial que no debe vivir en ningun
# servidor.

set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DESTINO_DB=""
RESPALDO=""
SALIDA_CONFIG=""
SALIDA_MEDIA=""

while [ $# -gt 0 ]; do
    case "$1" in
        --destino)       DESTINO_DB="$2"; shift 2 ;;
        --respaldo)      RESPALDO="$2"; shift 2 ;;
        --config-en)     SALIDA_CONFIG="$2"; shift 2 ;;
        --medios-en)     SALIDA_MEDIA="$2"; shift 2 ;;
        *) die "argumento desconocido: $1" ;;
    esac
done

[ -n "$DESTINO_DB" ] || die "falta --destino <nombre_de_base>. Nunca se restaura sobre la base de produccion."
[ "$DESTINO_DB" != "$LV_PGDATABASE" ] \
    || die "--destino coincide con la base de PRODUCCION ($LV_PGDATABASE). El ejercicio de restauracion no la toca en ningun paso."

require_cmd gpg "Instalar gnupg."
[ -n "$LV_BACKUP_REMOTE_PATH" ] || die "LV_BACKUP_REMOTE_PATH esta vacio."

if [ -z "$RESPALDO" ]; then
    RESPALDO="$(remote_list | head -n 1)"
    [ -n "$RESPALDO" ] || die "no hay respaldos en el destino remoto."
fi
log "Restaurando desde $RESPALDO hacia la base '$DESTINO_DB'"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- 1. Descargar y descifrar ------------------------------------------------

log "Descargando..."
remote_get "$RESPALDO" "$TMP/artefacto.gpg"

log "Descifrando (requiere la clave privada custodiada)..."
gpg --batch --yes --decrypt --output "$TMP/payload.tar" "$TMP/artefacto.gpg" 2>/dev/null \
    || die "no se pudo descifrar. Sin la clave privada correcta el respaldo es ilegible: es lo que se pretende."

tar -xf "$TMP/payload.tar" -C "$TMP"
CONTENIDO="$TMP/$LV_BACKUP_PREFIX"
[ -f "$CONTENIDO/database.dump" ] || die "el respaldo no contiene database.dump"

if [ -f "$CONTENIDO/MANIFEST.json" ]; then
    log "Manifiesto del respaldo:"
    sed 's/^/    /' "$CONTENIDO/MANIFEST.json" >&2
fi

# --- 2. Restaurar la base ----------------------------------------------------

log "Creando la base '$DESTINO_DB'..."
if [ -n "$LV_DB_CONTAINER" ]; then
    docker exec -i "$LV_DB_CONTAINER" psql -U "$LV_PGUSER" -d postgres \
        -c "DROP DATABASE IF EXISTS \"$DESTINO_DB\";" >/dev/null
    docker exec -i "$LV_DB_CONTAINER" psql -U "$LV_PGUSER" -d postgres \
        -c "CREATE DATABASE \"$DESTINO_DB\";" >/dev/null
    docker exec -i "$LV_DB_CONTAINER" pg_restore -U "$LV_PGUSER" -d "$DESTINO_DB" --no-owner \
        < "$CONTENIDO/database.dump" || log "AVISO: pg_restore reporto incidencias (habitual con --no-owner)"
else
    psql -h "$LV_PGHOST" -p "$LV_PGPORT" -U "$LV_PGUSER" -d postgres \
        -c "DROP DATABASE IF EXISTS \"$DESTINO_DB\";" >/dev/null
    psql -h "$LV_PGHOST" -p "$LV_PGPORT" -U "$LV_PGUSER" -d postgres \
        -c "CREATE DATABASE \"$DESTINO_DB\";" >/dev/null
    pg_restore -h "$LV_PGHOST" -p "$LV_PGPORT" -U "$LV_PGUSER" -d "$DESTINO_DB" --no-owner \
        "$CONTENIDO/database.dump" || log "AVISO: pg_restore reporto incidencias (habitual con --no-owner)"
fi
log "Base restaurada."

# --- 3. Configuracion y medios ----------------------------------------------

if [ -n "$SALIDA_CONFIG" ] && [ -d "$CONTENIDO/config" ]; then
    mkdir -p "$SALIDA_CONFIG"
    cp -R "$CONTENIDO/config/." "$SALIDA_CONFIG/"
    log "Configuracion restaurada en $SALIDA_CONFIG"
fi

if [ -n "$SALIDA_MEDIA" ] && [ -f "$CONTENIDO/media.tar.gz" ]; then
    mkdir -p "$SALIDA_MEDIA"
    tar -xzf "$CONTENIDO/media.tar.gz" -C "$SALIDA_MEDIA"
    log "Medios restaurados en $SALIDA_MEDIA"
fi

log "RESTAURACION COMPLETA hacia '$DESTINO_DB'."
log "Falta la comprobacion FUNCIONAL: backend/scripts/verificar_restauracion.py"
log "Contar filas o comparar bytes no demuestra que el sistema funcione."
