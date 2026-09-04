#!/usr/bin/env bash
# Ejercicio de restauracion, de punta a punta (spec 04, criterios 8, 10 y 11).
#
# Respalda, verifica, restaura sobre una base LIMPIA y comprueba funcionalmente
# el resultado, cronometrando cada fase. Deja evidencia fechada en
# docs/infra/backups/drills/.
#
# Se corre TRIMESTRALMENTE y despues de cualquier cambio material al sistema de
# respaldo. Sin el registro fechado, el requisito trimestral no es auditable: un
# procedimiento escrito y nunca ejecutado tiene el mismo valor que un respaldo
# nunca restaurado.
#
#   ./scripts/backup/drill.sh
#
# La base de produccion NO se toca en ningun paso: la restauracion va siempre a
# una base aparte, y `restore.sh` se niega si el destino coincide con ella.

set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$RAIZ/scripts/backup/lib.sh"

DESTINO_DB="${LV_DRILL_DB:-lejovista_drill}"
EVIDENCIA_DIR="${LV_DRILL_EVIDENCE_DIR:-$RAIZ/docs/infra/backups/drills}"
FECHA="$(date -u +%Y-%m-%d)"
EVIDENCIA="$EVIDENCIA_DIR/${FECHA}-drill.md"
SALIDA_TMP="$(mktemp -d)"
trap 'rm -rf "$SALIDA_TMP"' EXIT

mkdir -p "$EVIDENCIA_DIR"

ahora_ms() { python3 -c 'import time;print(int(time.time()*1000))'; }
INICIO_TOTAL="$(ahora_ms)"
declare -a FASES

fase() {  # fase <nombre> <comando...>
    local nombre="$1"; shift
    local t0 t1 estado
    log "--- FASE: $nombre ---"
    t0="$(ahora_ms)"
    if "$@" > "$SALIDA_TMP/$nombre.log" 2>&1; then estado="OK"; else estado="FALLA"; fi
    t1="$(ahora_ms)"
    FASES+=("$nombre|$estado|$(( (t1 - t0) / 1000 ))")
    log "    $nombre: $estado ($(( (t1 - t0) / 1000 ))s)"
    [ "$estado" = "OK" ] || log "    ultimas lineas: $(tail -3 "$SALIDA_TMP/$nombre.log" | tr '\n' ' ')"
}

fase respaldo  "$RAIZ/scripts/backup/backup.sh"
fase verificacion "$RAIZ/scripts/backup/verify.sh"
fase restauracion "$RAIZ/scripts/backup/restore.sh" \
    --destino "$DESTINO_DB" \
    --config-en "$SALIDA_TMP/config" \
    --medios-en "$SALIDA_TMP/media"

# La comprobacion funcional usa la ENCRYPTION_KEY que venia DENTRO del respaldo,
# no la del entorno: es lo que prueba que el conjunto "configuracion" sirve.
CLAVE_DEL_RESPALDO=""
for f in "$SALIDA_TMP/config"/*; do
    [ -f "$f" ] || continue
    v="$(grep -E '^ENCRYPTION_KEY=' "$f" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
    [ -n "$v" ] && CLAVE_DEL_RESPALDO="$v" && break
done
[ -n "$CLAVE_DEL_RESPALDO" ] || log "AVISO: el respaldo no traia ENCRYPTION_KEY; se usara la del entorno."

# La comprobacion funcional se conecta a la base RESTAURADA. `LV_PGHOST` puede
# ser un host TCP o un directorio de socket unix (como en un drill local, o en un
# host donde PostgreSQL no escucha en red): asyncpg necesita formas distintas
# para cada uno. Lo descubrio el primer drill.
url_de_la_base_restaurada() {
    if [ -n "${LV_DRILL_DATABASE_URL:-}" ]; then
        printf '%s' "$LV_DRILL_DATABASE_URL"
    elif [ "${LV_PGHOST:0:1}" = "/" ]; then
        printf 'postgresql+asyncpg://%s@/%s?host=%s&port=%s' \
            "$LV_PGUSER" "$DESTINO_DB" "$LV_PGHOST" "$LV_PGPORT"
    else
        printf 'postgresql+asyncpg://%s@%s:%s/%s' \
            "$LV_PGUSER" "$LV_PGHOST" "$LV_PGPORT" "$DESTINO_DB"
    fi
}

comprobar_funcional() {
    cd "$RAIZ/backend"
    DATABASE_URL="$(url_de_la_base_restaurada)" \
    ENCRYPTION_KEY="${CLAVE_DEL_RESPALDO:-${ENCRYPTION_KEY:-}}" \
    SECRET_KEY="${SECRET_KEY:-drill}" \
    MOCK_SERVICES=true \
    "${LV_PYTHON:-python3}" scripts/verificar_restauracion.py
}
fase comprobacion_funcional comprobar_funcional

FIN_TOTAL="$(ahora_ms)"
TOTAL_S=$(( (FIN_TOTAL - INICIO_TOTAL) / 1000 ))
TODO_OK="si"
for f in "${FASES[@]}"; do
    [ "$(echo "$f" | cut -d'|' -f2)" = "OK" ] || TODO_OK="no"
done

# --- Evidencia fechada -------------------------------------------------------

{
    echo "# Drill de respaldo y restauración — $FECHA"
    echo
    echo "> Evidencia del ejercicio que exige la spec"
    echo "> [04](../../../specs/04-backups-cifrados-y-restauracion-verificada.md),"
    echo "> criterios 8, 10 y 11. Generado por \`scripts/backup/drill.sh\`."
    echo
    echo "| Dato | Valor |"
    echo "|------|-------|"
    echo "| Fecha (UTC) | $(date -u +%Y-%m-%dT%H:%M:%SZ) |"
    echo "| Ejecutado por | $(whoami)@$(hostname) |"
    echo "| Driver del remoto | \`$LV_BACKUP_REMOTE_DRIVER\` |"
    echo "| Base de destino | \`$DESTINO_DB\` (nunca producción) |"
    echo "| Duración total | **${TOTAL_S}s** |"
    echo "| RTO objetivo | 8 h (28800 s) |"
    echo "| Dentro del objetivo | $([ "$TOTAL_S" -lt 28800 ] && echo "sí" || echo "**NO**") |"
    echo "| Resultado | $([ "$TODO_OK" = "si" ] && echo "**RESTAURACIÓN UTILIZABLE**" || echo "**FALLÓ**") |"
    echo
    echo "## Duración por fase"
    echo
    echo "| Fase | Resultado | Segundos |"
    echo "|------|-----------|----------|"
    for f in "${FASES[@]}"; do
        printf '| %s | %s | %s |\n' \
            "$(echo "$f" | cut -d'|' -f1)" \
            "$(echo "$f" | cut -d'|' -f2)" \
            "$(echo "$f" | cut -d'|' -f3)"
    done
    echo
    echo "## Comprobación funcional"
    echo
    echo "Cuatro cosas que tienen que poder hacerse sobre la base restaurada. Contar"
    echo "filas o comparar bytes no demuestra ninguna."
    echo
    echo '```'
    cat "$SALIDA_TMP/comprobacion_funcional.log" 2>/dev/null || echo "(sin salida)"
    echo '```'
    echo
    echo "## Verificación del respaldo remoto"
    echo
    echo '```'
    tail -12 "$SALIDA_TMP/verificacion.log" 2>/dev/null || echo "(sin salida)"
    echo '```'
    echo
    echo "## Hallazgos"
    echo
    if [ "$TODO_OK" = "si" ]; then
        echo "- Ninguno. El ejercicio completó todas las fases."
    else
        echo "- El ejercicio **falló**. Fases en error:"
        for f in "${FASES[@]}"; do
            if [ "$(echo "$f" | cut -d'|' -f2)" != "OK" ]; then
                echo "  - \`$(echo "$f" | cut -d'|' -f1)\`:"
                echo '    ```'
                tail -20 "$SALIDA_TMP/$(echo "$f" | cut -d'|' -f1).log" 2>/dev/null | sed 's/^/    /'
                echo '    ```'
            fi
        done
    fi
} > "$EVIDENCIA"

log "Evidencia escrita en $EVIDENCIA"
log "Duracion total: ${TOTAL_S}s — resultado: $TODO_OK"
[ "$TODO_OK" = "si" ]
