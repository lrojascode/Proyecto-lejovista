#!/usr/bin/env bash
# OBSOLETO — sustituido por scripts/backup/restore.sh (spec 04).
# Restauraba desde una copia LOCAL sin cifrar. Ver docs/infra/backups/README.md.
set -euo pipefail

# ==============================================================================
# LejoVista - Postgres Database Restore Script
# Uso: ./scripts/restore_postgres.sh <archivo_backup.dump> [target_db_name]
# ==============================================================================

if [ $# -lt 1 ]; then
    echo "Uso: $0 <archivo_backup.dump> [target_db_name]"
    exit 1
fi

BACKUP_FILE="$1"
TARGET_DB="${2:-lejovista_restore_test}"
CONTAINER_NAME="${DB_CONTAINER:-lejovista-db-1}"
DB_USER="${POSTGRES_USER:-lejovista}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: El archivo $BACKUP_FILE no existe."
    exit 1
fi

echo "=== Restauración de Base de Datos de LejoVista ==="
echo "Archivo: $BACKUP_FILE"
echo "Base de datos destino: $TARGET_DB"

# Buscar contenedor activo
if ! docker ps --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
    CONTAINER_NAME=$(docker ps --filter "ancestor=postgres:16-alpine" --format '{{.Names}}' | head -n 1)
fi

if [ -z "$CONTAINER_NAME" ]; then
    echo "ERROR: No se encontró un contenedor PostgreSQL activo."
    exit 1
fi

echo "Usando contenedor: $CONTAINER_NAME"

# 1. Crear base de datos limpia de prueba
echo "Preparando base de datos $TARGET_DB..."
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -c "DROP DATABASE IF EXISTS $TARGET_DB;"
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -c "CREATE DATABASE $TARGET_DB;"

# 2. Restaurar dump con pg_restore
echo "Restaurando dump con pg_restore..."
START_TIME=$(date +%s)
cat "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" pg_restore -U "$DB_USER" -d "$TARGET_DB" --no-owner --role="$DB_USER" || true
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo "SUCCESS: Restauración completada en ${DURATION}s."

# 3. Verificación de conteo de tablas y filas
echo "=== Resumen de Tablas Restauradas en $TARGET_DB ==="
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$TARGET_DB" -c "
SELECT table_name, (xpath('/row/cnt/text()', xml_count))[1]::text::int as row_count
FROM (
  SELECT table_name,
         query_to_xml(format('select count(*) as cnt from %I', table_name), false, true, '') as xml_count
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
) t ORDER BY table_name;
"
