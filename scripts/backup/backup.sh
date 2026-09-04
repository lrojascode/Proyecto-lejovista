#!/usr/bin/env bash
# Respaldo diario cifrado hacia un destino externo al VPS (spec 04).
#
# Un solo artefacto por corrida, con los TRES conjuntos que la spec exige:
#
#   base de datos  — volcado consistente (`pg_dump -Fc`)
#   configuracion  — las variables y secretos necesarios para levantar el sistema
#   medios         — los archivos subidos por los clientes
#
# Van juntos a proposito: restaurar la base sin la configuracion deja una base
# que no arranca, y sin ENCRYPTION_KEY las credenciales de Meta guardadas quedan
# ilegibles para siempre. Separarlos invita a respaldar uno y olvidar el otro.
#
#   ./scripts/backup/backup.sh
#
# Configuracion: ver scripts/backup/lib.sh y docs/infra/backups/README.md.

set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FPR="$(preflight_comun)"
SELLO="$(date -u +%Y%m%dT%H%M%SZ)"
NOMBRE="${LV_BACKUP_PREFIX}_${SELLO}_full.tar.gpg"

TRABAJO="$(mktemp -d "${LV_BACKUP_WORKDIR%/}.XXXXXX" 2>/dev/null || mktemp -d)"
STAGE="$TRABAJO/$LV_BACKUP_PREFIX"
mkdir -p "$STAGE"
# Se limpia SIEMPRE: el material en claro no puede quedar en el disco del VPS
# despues de la corrida, ni siquiera si algo falla a la mitad.
trap 'rm -rf "$TRABAJO"' EXIT

log "Respaldo $SELLO — destino: $LV_BACKUP_REMOTE_DRIVER:$LV_BACKUP_REMOTE_PATH"

# --- 1. Base de datos --------------------------------------------------------

log "Volcando la base de datos..."
if [ -n "$LV_DB_CONTAINER" ]; then
    docker exec -i "$LV_DB_CONTAINER" \
        pg_dump -U "$LV_PGUSER" -d "$LV_PGDATABASE" -Fc > "$STAGE/database.dump"
else
    PGPASSWORD="${PGPASSWORD:-}" pg_dump \
        -h "$LV_PGHOST" -p "$LV_PGPORT" -U "$LV_PGUSER" -d "$LV_PGDATABASE" \
        -Fc > "$STAGE/database.dump"
fi
[ -s "$STAGE/database.dump" ] || die "el volcado de la base salio vacio."
log "  base de datos: $(wc -c < "$STAGE/database.dump") bytes"

# --- 2. Configuracion --------------------------------------------------------

mkdir -p "$STAGE/config"
if [ -n "$LV_CONFIG_FILES" ]; then
    for f in $LV_CONFIG_FILES; do
        if [ -f "$f" ]; then
            cp "$f" "$STAGE/config/$(basename "$f")"
            log "  configuracion: $(basename "$f")"
        else
            log "  AVISO: no existe el archivo de configuracion $f"
        fi
    done
else
    log "  AVISO: LV_CONFIG_FILES vacio. Sin ENCRYPTION_KEY, una restauracion deja las credenciales de Meta ilegibles."
fi

# --- 3. Medios ---------------------------------------------------------------

if [ -n "$LV_MEDIA_DIR" ] && [ -d "$LV_MEDIA_DIR" ]; then
    log "Empaquetando medios desde $LV_MEDIA_DIR..."
    tar -czf "$STAGE/media.tar.gz" -C "$(dirname "$LV_MEDIA_DIR")" "$(basename "$LV_MEDIA_DIR")"
    log "  medios: $(wc -c < "$STAGE/media.tar.gz") bytes"
else
    log "  sin medios que respaldar (LV_MEDIA_DIR vacio o inexistente)."
fi

# --- 4. Manifiesto -----------------------------------------------------------
#
# Va DENTRO del artefacto cifrado: es lo que permite que una restauracion sepa
# que esta restaurando sin tener que abrirlo entero primero.

cat > "$STAGE/MANIFEST.json" <<JSON
{
  "prefijo": "$LV_BACKUP_PREFIX",
  "sello": "$SELLO",
  "creado_en": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host": "$(hostname)",
  "base_de_datos": "$LV_PGDATABASE",
  "incluye_medios": $([ -f "$STAGE/media.tar.gz" ] && echo true || echo false),
  "archivos_de_configuracion": $(ls -1 "$STAGE/config" 2>/dev/null | wc -l | tr -d ' '),
  "cifrado_para": "$FPR"
}
JSON

# --- 5. Empaquetar y cifrar EN ORIGEN ----------------------------------------
#
# El destino se trata como no confiable: solo sale texto cifrado. `--recipient-file`
# usa la clave publica directamente, sin depender de un llavero con estado en el
# servidor; `--trust-model always` evita el dialogo de confianza en modo batch.

log "Cifrando para $FPR..."
tar -cf "$TRABAJO/payload.tar" -C "$TRABAJO" "$LV_BACKUP_PREFIX"
gpg --batch --yes --trust-model always \
    --recipient-file "$LV_BACKUP_PUBKEY" \
    --compress-algo zlib \
    --encrypt --output "$TRABAJO/$NOMBRE" "$TRABAJO/payload.tar"
rm -f "$TRABAJO/payload.tar"
TAMANO="$(wc -c < "$TRABAJO/$NOMBRE" | tr -d ' ')"
log "  artefacto cifrado: $TAMANO bytes"

# --- 6. Subir ----------------------------------------------------------------

log "Subiendo a $LV_BACKUP_REMOTE_DRIVER..."
remote_put "$TRABAJO/$NOMBRE" "$NOMBRE"

# La suma viaja al lado del artefacto. Sin ella, la verificacion solo puede mirar
# la CABECERA OpenPGP, y un archivo truncado por el cuerpo la conserva intacta:
# pasaria la comprobacion diaria y fallaria el dia del incidente. Es exactamente
# el modo de fallo que esta verificacion existe para detectar.
SUMA="$(sha256_de "$TRABAJO/$NOMBRE")"
printf '%s  %s\n' "$SUMA" "$NOMBRE" > "$TRABAJO/$NOMBRE.sha256"
remote_put "$TRABAJO/$NOMBRE.sha256" "$NOMBRE.sha256"
log "  suma sha256: $SUMA"

# El criterio de exito es que la copia REMOTA este, no que el volcado local se
# haya escrito. Se relee la lista del remoto para confirmarlo.
remote_list | grep -qx "$NOMBRE" \
    || die "el respaldo no aparece en el destino remoto despues de subirlo."
log "  confirmado en el remoto: $NOMBRE"

# --- 7. Retencion ------------------------------------------------------------

if [ "$LV_BACKUP_RETENTION_DAYS" -gt 0 ]; then
    CORTE=$(( $(date -u +%s) - LV_BACKUP_RETENTION_DAYS * 86400 ))
    while read -r viejo; do
        [ -n "$viejo" ] || continue
        epoch="$(epoch_de_nombre "$viejo" || true)"
        [ -n "$epoch" ] || continue
        if [ "$epoch" -lt "$CORTE" ]; then
            log "  retencion: se elimina $viejo"
            remote_delete "$viejo"
        fi
    done <<< "$(remote_list)"
fi

# --- 8. Senal monitorizable + latido externo ---------------------------------

mkdir -p "$(dirname "${LV_BACKUP_STATUS_FILE:-/var/backups/lejovista/estado.json}")" 2>/dev/null || true
cat > "${LV_BACKUP_STATUS_FILE:-/var/backups/lejovista/estado.json}" <<JSON || true
{
  "ultimo_respaldo": "$NOMBRE",
  "sello": "$SELLO",
  "tamano_bytes": $TAMANO,
  "destino": "$LV_BACKUP_REMOTE_DRIVER",
  "resultado": "ok",
  "verificado_en": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

if [ -n "$LV_BACKUP_HEARTBEAT_URL" ]; then
    # Interruptor de hombre muerto: la ALERTA es la AUSENCIA de este ping. Si el
    # VPS esta caido no puede avisar de nada, y ese es justamente el escenario.
    curl -fsS --max-time 15 "$LV_BACKUP_HEARTBEAT_URL" >/dev/null 2>&1 \
        && log "  latido externo enviado" \
        || log "  AVISO: no se pudo enviar el latido externo"
fi

log "Respaldo completado: $NOMBRE"
