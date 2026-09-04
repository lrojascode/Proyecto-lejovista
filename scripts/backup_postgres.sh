#!/usr/bin/env bash
# OBSOLETO — sustituido por scripts/backup/ (spec 04).
#
# Este script escribia y leia EN EL MISMO VPS que protege, sin cifrar, y solo
# cubria la base de datos: ni la configuracion ni los medios. Un fallo de disco
# se llevaba la base y sus copias a la vez, y sin ENCRYPTION_KEY una
# restauracion dejaba las credenciales de Meta ilegibles.
#
# Usar en su lugar:
#   scripts/backup/backup.sh   — respaldo cifrado hacia un destino externo
#   scripts/backup/verify.sh   — verificacion diaria con alerta externa
#   scripts/backup/restore.sh  — restauracion desde host limpio
#   scripts/backup/drill.sh    — ejercicio trimestral con evidencia
#
# Ver docs/infra/backups/README.md. Se conserva mientras la transicion no este
# confirmada en produccion; no se retira hasta que el primer drill contra el
# remoto real haya pasado.
set -euo pipefail

# ==============================================================================
# LejoVista - Postgres Automated Backup Script
# Retención: 7 diarios, 4 semanales, 3 mensuales
# Formato: pg_dump -Fc (custom compressed format)
# ==============================================================================

BACKUP_DIR="${BACKUP_DIR:-/var/backups/lejovista/postgres}"
CONTAINER_NAME="${DB_CONTAINER:-lejovista-db-1}"
DB_USER="${POSTGRES_USER:-lejovista}"
DB_NAME="${POSTGRES_DB:-lejovista}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DAY_OF_WEEK=$(date +"%u")   # 1 (Monday) to 7 (Sunday)
DAY_OF_MONTH=$(date +"%d")  # 01 to 31

DIR_DAILY="$BACKUP_DIR/daily"
DIR_WEEKLY="$BACKUP_DIR/weekly"
DIR_MONTHLY="$BACKUP_DIR/monthly"

mkdir -p "$DIR_DAILY" "$DIR_WEEKLY" "$DIR_MONTHLY"

BACKUP_FILE="$DIR_DAILY/lejovista_${TIMESTAMP}.dump"
LOG_FILE="$BACKUP_DIR/backup.log"

log() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $1" | tee -a "$LOG_FILE"
}

log "INFO: Iniciando backup de Postgres (base: $DB_NAME)..."

# Ejecutar pg_dump dentro del contenedor
if docker ps --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$BACKUP_FILE"
else
    CONTAINER_ALT=$(docker ps --filter "name=db" --filter "ancestor=postgres:16-alpine" --format '{{.Names}}' | head -n 1)
    if [ -n "$CONTAINER_ALT" ]; then
        docker exec "$CONTAINER_ALT" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$BACKUP_FILE"
    else
        log "ERROR: No se encontró el contenedor de PostgreSQL activo ($CONTAINER_NAME o db)."
        exit 1
    fi
fi

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
log "SUCCESS: Backup diario completado: $BACKUP_FILE (Tamaño: $FILE_SIZE)"

# 1. Copia semanal (los domingos = 7)
if [ "$DAY_OF_WEEK" -eq 7 ]; then
    WEEKLY_FILE="$DIR_WEEKLY/lejovista_weekly_${TIMESTAMP}.dump"
    cp "$BACKUP_FILE" "$WEEKLY_FILE"
    log "INFO: Copia semanal guardada en $WEEKLY_FILE"
fi

# 2. Copia mensual (día 01)
if [ "$DAY_OF_MONTH" = "01" ]; then
    MONTHLY_FILE="$DIR_MONTHLY/lejovista_monthly_${TIMESTAMP}.dump"
    cp "$BACKUP_FILE" "$MONTHLY_FILE"
    log "INFO: Copia mensual guardada en $MONTHLY_FILE"
fi

# 3. Aplicar políticas de retención
log "INFO: Aplicando rotación y políticas de retención..."

# Retener últimos 7 diarios
cd "$DIR_DAILY"
ls -tp lejovista_*.dump 2>/dev/null | grep -v '/$' | tail -n +8 | xargs -I {} rm -- {} 2>/dev/null || true

# Retener últimos 4 semanales
cd "$DIR_WEEKLY"
ls -tp lejovista_weekly_*.dump 2>/dev/null | grep -v '/$' | tail -n +5 | xargs -I {} rm -- {} 2>/dev/null || true

# Retener últimos 3 mensuales
cd "$DIR_MONTHLY"
ls -tp lejovista_monthly_*.dump 2>/dev/null | grep -v '/$' | tail -n +4 | xargs -I {} rm -- {} 2>/dev/null || true

log "SUCCESS: Proceso de backup y rotación finalizado con éxito."
