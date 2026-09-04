#!/usr/bin/env bash
# Vigilante EXTERNO al VPS (spec 13, criterio 12).
#
# Corre en otra máquina. Ese es el punto entero: un sistema que solo alerta
# desde dentro no puede informar de su propia muerte. Si el VPS se apaga, este
# script no recibe respuesta, y esa AUSENCIA es la alerta.
#
# Comprueba tres cosas, en este orden:
#
#   1. Que el endpoint responda.
#   2. Que `generado_en` esté FRESCO. Un endpoint que responde con una marca de
#      hace dos horas es un proceso zombi: contesta y no mide nada.
#   3. Que no haya señales en alerta.
#
# La segunda es la que la gente olvida, y es la que atrapa el caso peor: el
# servicio arriba y el trabajo parado.
#
# Instalación (en la máquina vigilante, NO en el VPS):
#
#   */5 * * * * /ruta/vigilar.sh >> /var/log/lejovista-vigilante.log 2>&1
set -Eeuo pipefail

URL="${LEJOVISTA_SENALES_URL:?Falta LEJOVISTA_SENALES_URL (ej. https://app.ejemplo.com/api/v1/operacion/senales)}"
WEBHOOK="${LEJOVISTA_ALERTA_WEBHOOK:-}"
# Cuántos segundos puede tener la marca antes de considerarse zombi. Debe ser
# mayor que el intervalo del cron, o alertaría en cada vuelta.
MAX_EDAD="${LEJOVISTA_MAX_EDAD_SEGUNDOS:-900}"
TIMEOUT="${LEJOVISTA_TIMEOUT:-15}"

avisar() {
  local nivel="$1" mensaje="$2"
  echo "[$(date -u +%FT%TZ)] $nivel: $mensaje"
  if [[ -n "$WEBHOOK" ]]; then
    # Best-effort: si el webhook falla, el mensaje ya quedó en el log de esta
    # máquina, que sigue viva aunque el VPS no.
    curl -fsS -m 10 -X POST "$WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "$(printf '{"nivel":%s,"mensaje":%s}' \
            "$(printf '%s' "$nivel" | jq -Rs .)" \
            "$(printf '%s' "$mensaje" | jq -Rs .)")" >/dev/null || \
      echo "  (no se pudo entregar la alerta al webhook)"
  fi
}

# --- 1. ¿Responde? ---------------------------------------------------------
if ! CUERPO="$(curl -fsS -m "$TIMEOUT" "$URL" 2>&1)"; then
  avisar CRITICO "El VPS no responde en $URL. Detalle: ${CUERPO:0:200}"
  exit 1
fi

if ! echo "$CUERPO" | jq -e . >/dev/null 2>&1; then
  avisar CRITICO "La respuesta de $URL no es JSON. ¿Hay un proxy delante devolviendo una página de error?"
  exit 1
fi

# --- 2. ¿La marca avanza? --------------------------------------------------
GENERADO="$(echo "$CUERPO" | jq -r '.generado_en // empty')"
if [[ -z "$GENERADO" ]]; then
  avisar CRITICO "La respuesta no trae 'generado_en'; no se puede saber si el dato es de ahora."
  exit 1
fi

# `date -d` en GNU, `-j -f` en BSD/macOS: el vigilante puede correr en cualquiera.
if EPOCH="$(date -u -d "$GENERADO" +%s 2>/dev/null)"; then :
elif EPOCH="$(date -u -j -f '%Y-%m-%dT%H:%M:%S' "${GENERADO%%.*}" +%s 2>/dev/null)"; then :
else
  avisar ADVERTENCIA "No se pudo interpretar la marca de tiempo '$GENERADO'."
  EPOCH=""
fi

if [[ -n "$EPOCH" ]]; then
  EDAD=$(( $(date -u +%s) - EPOCH ))
  if (( EDAD > MAX_EDAD )); then
    avisar CRITICO "El endpoint responde pero su dato es de hace ${EDAD}s (tope ${MAX_EDAD}s). El proceso está vivo y no está midiendo."
    exit 1
  fi
fi

# --- 3. ¿Hay señales en alerta? -------------------------------------------
ESTADO="$(echo "$CUERPO" | jq -r '.estado // "desconocido"')"
if [[ "$ESTADO" == "alerta" ]]; then
  DETALLE="$(echo "$CUERPO" | jq -r '
    [.senales[] | select(.estado == "alerta")
     | "\(.titulo): \(.valor) (umbral \(.umbral))"] | join(" · ")')"
  avisar ALERTA "$DETALLE"
  exit 1
fi

echo "[$(date -u +%FT%TZ)] ok: sin alertas, dato de hace ${EDAD:-?}s."
