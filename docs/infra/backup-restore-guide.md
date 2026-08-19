# Guía de Backups y Restauración de PostgreSQL — LejoVista

## 1. Estrategia de Backups

La base de datos de PostgreSQL en producción (Coolify) utiliza respaldos automáticos en formato custom comprimido (`pg_dump -Fc`).

### Características del Formato `-Fc`
- Comprimido con zlib (menor espacio en disco).
- Permite restauraciones selectivas de tablas o esquemas con `pg_restore`.
- Es el formato estándar y más confiable para migraciones y recuperaciones de desastres en PostgreSQL.

### Política de Retención
- **Diarios:** Se conservan las últimas **7 copias** en `/var/backups/lejovista/postgres/daily/`.
- **Semanales:** Se copia el backup de cada domingo a `/var/backups/lejovista/postgres/weekly/` (últimas **4 semanas**).
- **Mensuales:** Se copia el backup del día 01 de cada mes a `/var/backups/lejovista/postgres/monthly/` (últimos **3 meses**).

---

## 2. Automatización con Cron

En el VPS / Host donde corre Coolify, programar la ejecución diaria a las 03:00 AM UTC:

```bash
# Editar crontab del root o usuario con acceso a Docker
crontab -e
```

Agregar la línea:
```cron
0 3 * * * /root/Proyectos/Proyecto\ lejovista/scripts/backup_postgres.sh >> /var/backups/lejovista/postgres/cron.log 2>&1
```

Variables de entorno configurables para el script:
- `BACKUP_DIR`: Directorio destino (default: `/var/backups/lejovista/postgres`).
- `DB_CONTAINER`: Nombre del contenedor Docker (default busca `lejovista-db-1` o postgres).
- `POSTGRES_USER`: Usuario de base de datos (default: `lejovista`).
- `POSTGRES_DB`: Nombre de base de datos (default: `lejovista`).

---

## 3. Guía de Restauración y Prueba

> [!IMPORTANT]
> Un backup no probado no es un backup. Siempre verifique la integridad de los dumps en una base de datos limpia.

### Procedimiento de Prueba de Restauración:

1. Ubique el archivo de backup más reciente:
   ```bash
   LATEST_BACKUP=$(ls -t /var/backups/lejovista/postgres/daily/*.dump | head -n 1)
   ```

2. Ejecute el script de restauración sobre una base de prueba aislada:
   ```bash
   ./scripts/restore_postgres.sh "$LATEST_BACKUP" lejovista_restore_test
   ```

3. El script ejecutará:
   - `DROP DATABASE IF EXISTS lejovista_restore_test;`
   - `CREATE DATABASE lejovista_restore_test;`
   - `pg_restore -U lejovista -d lejovista_restore_test ...`
   - Medición exacta del tiempo de ejecución.
   - Conteo de registros por cada tabla del esquema `public`.

### Procedimiento de Restauración en Producción (Disaster Recovery):

1. Detener servicios de aplicación para evitar escrituras concurrentes:
   ```bash
   docker stop lejovista-backend-1 lejovista-celery-worker-creation-1 lejovista-celery-worker-sync-1
   ```

2. Restaurar la base de datos principal:
   ```bash
   ./scripts/restore_postgres.sh /var/backups/lejovista/postgres/daily/lejovista_YYYYMMDD_HHMMSS.dump lejovista
   ```

3. Reiniciar los servicios de aplicación:
   ```bash
   docker start lejovista-backend-1 lejovista-celery-worker-creation-1 lejovista-celery-worker-sync-1
   ```
