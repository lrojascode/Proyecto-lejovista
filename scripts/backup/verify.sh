#!/usr/bin/env bash
# Verificacion diaria del respaldo remoto (spec 04, criterios 5 y 6).
#
# Comprueba tres cosas sobre el respaldo remoto mas reciente:
#
#   existe      — hay al menos un artefacto con el prefijo esperado
#   es legible  — su cabecera OpenPGP se puede analizar y esta dirigida a NUESTRA
#                 clave. Sin descifrar: la clave privada no vive aca.
#   es reciente — su antiguedad esta dentro del RPO declarado (24 h por defecto)
#
# Sale con codigo != 0 cuando alguna falla, para que cron/monitorizacion lo
# detecte. Escribe ademas una senal monitorizable en JSON.
#
#   ./scripts/backup/verify.sh

set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FPR="$(preflight_comun)"
ESTADO_FILE="${LV_BACKUP_STATUS_FILE:-/var/backups/lejovista/estado.json}"

fallar() {
    local motivo="$1"
    log "VERIFICACION FALLIDA: $motivo"
    mkdir -p "$(dirname "$ESTADO_FILE")" 2>/dev/null || true
    cat > "$ESTADO_FILE" <<JSON || true
{
  "resultado": "fallo",
  "motivo": "$motivo",
  "verificado_en": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
    exit 1
}

# --- 1. Existe ---------------------------------------------------------------

ULTIMO="$(remote_list | head -n 1)"
[ -n "$ULTIMO" ] || fallar "no hay ningun respaldo en el destino remoto"
log "Ultimo respaldo remoto: $ULTIMO"

# --- 2. Es reciente ----------------------------------------------------------
#
# La antiguedad sale del NOMBRE, no de la fecha del archivo remoto: los
# proveedores de objetos reescriben mtime al copiar, y con eso un respaldo viejo
# parece nuevo. Justo el fallo que hay que detectar.

EPOCH="$(epoch_de_nombre "$ULTIMO" || true)"
[ -n "$EPOCH" ] || fallar "el nombre '$ULTIMO' no lleva un sello de tiempo legible"
EDAD_H=$(( ( $(date -u +%s) - EPOCH ) / 3600 ))
log "Antiguedad: ${EDAD_H}h (RPO objetivo: ${LV_RPO_HOURS}h)"

# --- 3. Es legible -----------------------------------------------------------
#
# Se descarga y se analiza la cabecera OpenPGP. Un artefacto truncado o corrupto
# no produce paquetes analizables; uno cifrado para otra clave no sirve, porque
# la privada que se custodia no lo abre. Las dos cosas son fallos silenciosos que
# solo se descubren el dia del incidente.

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
remote_get "$ULTIMO" "$TMP/artefacto.gpg" || fallar "no se pudo descargar $ULTIMO del remoto"
[ -s "$TMP/artefacto.gpg" ] || fallar "el respaldo remoto $ULTIMO esta vacio"

# Integridad del CUERPO, no solo de la cabecera. La suma se genero al crear el
# respaldo y viaja al lado; recalcularla detecta truncados y corrupcion en
# transito o en reposo, sin necesitar la clave privada (que no vive aca).
SUMA_ESPERADA=""
if remote_get "$ULTIMO.sha256" "$TMP/suma.txt" 2>/dev/null; then
    SUMA_ESPERADA="$(awk '{print $1}' "$TMP/suma.txt")"
fi
if [ -n "$SUMA_ESPERADA" ]; then
    SUMA_REAL="$(sha256_de "$TMP/artefacto.gpg")"
    [ "$SUMA_REAL" = "$SUMA_ESPERADA" ] \
        || fallar "$ULTIMO no coincide con su suma sha256 (esperada ${SUMA_ESPERADA:0:12}…, obtenida ${SUMA_REAL:0:12}…): esta truncado o corrupto"
    log "Suma sha256 verificada (${SUMA_REAL:0:12}…)"
else
    # Respaldos anteriores a que se publicara la suma. Se avisa en vez de
    # fallar, para no romper la verificacion durante el despliegue.
    log "AVISO: $ULTIMO no tiene suma publicada; solo se verifica la cabecera."
fi

PAQUETES="$(gpg --batch --list-packets "$TMP/artefacto.gpg" 2>/dev/null || true)"
printf '%s' "$PAQUETES" | grep -q "pubkey enc packet" \
    || fallar "$ULTIMO no es un artefacto OpenPGP legible (truncado o corrupto)"

# El artefacto tiene que estar dirigido a NUESTRA clave. Se acepta cualquiera de
# sus keyid —el primario o el de una subclave— porque en OpenPGP quien cifra es
# la subclave de cifrado, no la primaria.
PAQUETES_MIN="$(printf '%s' "$PAQUETES" | tr 'A-Z' 'a-z')"
DIRIGIDO_A=""
while read -r kid; do
    [ -n "$kid" ] || continue
    if printf '%s' "$PAQUETES_MIN" | grep -q "keyid $kid"; then DIRIGIDO_A="$kid"; break; fi
done <<< "$(keyids_de_la_clave)"

[ -n "$DIRIGIDO_A" ] \
    || fallar "$ULTIMO no esta cifrado para la clave de respaldo custodiada ($FPR)"

log "Cabecera OpenPGP valida y dirigida a $DIRIGIDO_A"

# --- 4. Veredicto ------------------------------------------------------------

RESULTADO="ok"
MOTIVO=""
if [ "$EDAD_H" -gt "$LV_RPO_HOURS" ]; then
    RESULTADO="fallo"
    MOTIVO="el respaldo mas reciente tiene ${EDAD_H}h, por encima del RPO de ${LV_RPO_HOURS}h"
fi

mkdir -p "$(dirname "$ESTADO_FILE")" 2>/dev/null || true
cat > "$ESTADO_FILE" <<JSON || true
{
  "resultado": "$RESULTADO",
  "motivo": "$MOTIVO",
  "ultimo_respaldo": "$ULTIMO",
  "antiguedad_horas": $EDAD_H,
  "rpo_horas": $LV_RPO_HOURS,
  "tamano_bytes": $(wc -c < "$TMP/artefacto.gpg" | tr -d ' '),
  "cifrado_para": "$FPR",
  "verificado_en": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

[ "$RESULTADO" = "ok" ] || fallar "$MOTIVO"

if [ -n "${LV_VERIFY_HEARTBEAT_URL:-}" ]; then
    curl -fsS --max-time 15 "$LV_VERIFY_HEARTBEAT_URL" >/dev/null 2>&1 \
        && log "latido de verificacion enviado" \
        || log "AVISO: no se pudo enviar el latido de verificacion"
fi

log "VERIFICACION OK — $ULTIMO, ${EDAD_H}h, $(wc -c < "$TMP/artefacto.gpg" | tr -d ' ') bytes"
