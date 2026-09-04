#!/usr/bin/env bash
#
# Puerta previa a una migracion que NO es compatible hacia atras
# (spec 14, criterios 11 y 12).
#
# La regla es que toda migracion sea aditiva y el rollback consista en volver a
# la imagen anterior. Cuando eso no se puede —una contraccion, un cambio de tipo
# que reescribe la tabla— el rollback deja de ser gratis y hacen falta dos cosas
# ANTES de aplicarla:
#
#   1. Un respaldo externo verificado, no uno que se supone que existe. Lo
#      comprueba `scripts/backup/verify.sh`, que ya mira que el artefacto remoto
#      exista, este cifrado para nuestra clave y sea mas nuevo que el RPO.
#   2. El modo de solo lectura encendido, para que nadie escriba mientras la
#      tabla se reescribe y para que lo que vea el cliente sea un aviso y no un
#      error.
#
# Sale distinto de cero si cualquiera de las dos falla. Es la diferencia entre un
# procedimiento y una lista de buenas intenciones.
#
# Uso:
#   TOKEN=<credencial de agencia> \
#   scripts/despliegue/preflight-migracion.sh https://api.lejovista.agenciakibo.com
set -uo pipefail

API="${1:-${API_URL:-}}"
if [[ -z "$API" ]]; then
  echo "Falta la URL de la API. Uso: $0 https://api.tudominio.com" >&2
  exit 2
fi
API="${API%/}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

fallos=0
echo "Preflight de migracion riesgosa — $API"
echo

# ── 1 · Respaldo externo verificado ─────────────────────────────────────────
echo "1. Respaldo externo"
if "$RAIZ/scripts/backup/verify.sh"; then
  echo "   [OK ] hay un respaldo remoto reciente, legible y cifrado para nuestra clave"
else
  echo "   [MAL] la verificacion del respaldo fallo — NO apliques la migracion"
  fallos=$((fallos + 1))
fi
echo

# ── 2 · Modo de solo lectura ────────────────────────────────────────────────
echo "2. Modo de solo lectura"
if [[ -z "${TOKEN:-}" ]]; then
  echo "   [MAL] falta TOKEN: no se pudo comprobar el estado de los interruptores"
  fallos=$((fallos + 1))
else
  panorama="$(curl -fsS --max-time 20 -H "Authorization: Bearer $TOKEN" \
    "$API/api/v1/operacion/interruptores" 2>/dev/null)" || panorama=""
  if [[ -z "$panorama" ]]; then
    echo "   [MAL] no se pudo leer /operacion/interruptores"
    fallos=$((fallos + 1))
  # El patron es exacto a proposito: `"solo_lectura"[^}]*true` encontraba el
  # `"encendida":true` de la primera capacidad y daba por activo el modo de
  # solo lectura estando apagado, que es el peor sentido en el que puede
  # equivocarse una puerta previa a una migracion destructiva.
  elif printf '%s' "$panorama" | grep -qE '"solo_lectura"[[:space:]]*:[[:space:]]*true'; then
    echo "   [OK ] el modo de solo lectura esta activo"
  else
    echo "   [MAL] el modo de solo lectura NO esta activo. Enciendelo primero:"
    echo
    echo "     curl -X PUT \"$API/api/v1/operacion/interruptores/solo_lectura\" \\"
    echo "       -H \"Authorization: Bearer \$TOKEN\" -H 'Content-Type: application/json' \\"
    echo "       -d '{\"encendido\": true, \"motivo\": \"ventana de mantenimiento: <migracion>\"}'"
    fallos=$((fallos + 1))
  fi
fi

echo
if [[ $fallos -eq 0 ]]; then
  echo "ADELANTE. Recorda apagar el modo de solo lectura al terminar."
  exit 0
fi
echo "NO APLIQUES LA MIGRACION: $fallos condiciones sin cumplir."
echo "El procedimiento completo: docs/infra/operacion/migraciones.md"
exit 1
