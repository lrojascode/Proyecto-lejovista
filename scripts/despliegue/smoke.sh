#!/usr/bin/env bash
#
# Comprobacion posterior al despliegue (spec 14, criterio 13).
#
# Un despliegue que termina en "up" no esta verificado, solo esta hecho. Esto
# recorre el minimo que tiene que funcionar para que el producto sirva de algo, y
# deja constancia fechada de lo que respondio.
#
#   1. Que corre el commit que se quiso desplegar, y no otro.
#   2. Que las migraciones que trae la imagen estan aplicadas.
#   3. Que se puede autenticar.
#   4. Que se lee el historico (base de datos, con sesion).
#   5. Que responden a la vez la base de datos y Redis.
#
# Uso:
#   COMMIT_ESPERADO=<sha> \
#   SMOKE_EMAIL=... SMOKE_PASSWORD=... \
#   scripts/despliegue/smoke.sh https://api.lejovista.agenciakibo.com
#
# El resultado se escribe en docs/infra/operacion/despliegues/<fecha>-<sha>.txt
# salvo que se fije SIN_EVIDENCIA=1. Sale distinto de cero si algo falla, para
# que un despliegue mal verificado no pase por bueno.
set -uo pipefail

API="${1:-${API_URL:-}}"
if [[ -z "$API" ]]; then
  echo "Falta la URL de la API. Uso: $0 https://api.tudominio.com" >&2
  exit 2
fi
API="${API%/}"

COMMIT_ESPERADO="${COMMIT_ESPERADO:-}"
SMOKE_EMAIL="${SMOKE_EMAIL:-}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-}"

fallos=0
inicio="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
salida=""

registrar() {  # registrar <OK|MAL> <paso> <detalle>
  local estado="$1"; shift
  local paso="$1"; shift
  local detalle="$*"
  printf '  [%s] %-28s %s\n' "$estado" "$paso" "$detalle"
  salida+="  [$estado] $paso — $detalle"$'\n'
  [[ "$estado" == "MAL" ]] && fallos=$((fallos + 1))
  return 0
}

json() { # json <cuerpo> <clave>  (sin dependencias: sed sobre JSON plano)
  printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" | head -1
}

echo "Smoke test de $API  ($inicio)"
echo

# ── 1 y 2 · Que corre lo que se desplego ────────────────────────────────────
version="$(curl -fsS --max-time 20 "$API/version" 2>/dev/null)" || version=""
if [[ -z "$version" ]]; then
  registrar MAL "version" "/version no respondio"
else
  commit="$(json "$version" commit)"
  al_dia="$(json "$version" migraciones_al_dia)"
  if [[ -n "$COMMIT_ESPERADO" && "$commit" != "$COMMIT_ESPERADO" ]]; then
    registrar MAL "commit desplegado" "corre $commit y se desplego $COMMIT_ESPERADO"
  else
    registrar OK "commit desplegado" "$commit"
  fi
  if [[ "$al_dia" == "true" ]]; then
    registrar OK "migraciones" "al dia ($(json "$version" migracion_aplicada))"
  else
    registrar MAL "migraciones" \
      "codigo en $(json "$version" migracion_en_codigo), base en $(json "$version" migracion_aplicada)"
  fi
fi

# ── 3 · Autenticacion ───────────────────────────────────────────────────────
token=""
if [[ -z "$SMOKE_EMAIL" || -z "$SMOKE_PASSWORD" ]]; then
  registrar MAL "autenticacion" "faltan SMOKE_EMAIL/SMOKE_PASSWORD; el recorrido con sesion no se probo"
else
  cuerpo="$(curl -fsS --max-time 20 -X POST "$API/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PASSWORD\"}" 2>/dev/null)" || cuerpo=""
  token="$(json "$cuerpo" access_token)"
  if [[ -n "$token" ]]; then
    registrar OK "autenticacion" "sesion emitida"
  else
    registrar MAL "autenticacion" "login no devolvio credencial"
  fi
fi

# ── 4 · Lectura del historico ───────────────────────────────────────────────
if [[ -n "$token" ]]; then
  codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -H "Authorization: Bearer $token" "$API/api/v1/campaigns/recent")"
  if [[ "$codigo" == "200" ]]; then
    registrar OK "lectura de historico" "campaigns/recent respondio 200"
  else
    registrar MAL "lectura de historico" "campaigns/recent respondio $codigo"
  fi
fi

# ── 5 · Base de datos y Redis a la vez ──────────────────────────────────────
# `/operacion/senales` publica la señal `dependencias`, que consulta las dos. Es
# el unico sitio donde una sola llamada las toca de verdad; que responda 200 no
# alcanza, porque responde igual con Redis caido: hay que mirar la señal.
senales="$(curl -fsS --max-time 30 "$API/api/v1/operacion/senales" 2>/dev/null)" || senales=""
if [[ -z "$senales" ]]; then
  registrar MAL "base de datos y Redis" "/operacion/senales no respondio"
elif printf '%s' "$senales" | grep -q '"dependencias"'; then
  if printf '%s' "$senales" | grep -qE '"dependencias"[^}]*"(alerta|desconocido)"'; then
    registrar MAL "base de datos y Redis" "la señal de dependencias no esta en verde"
  else
    registrar OK "base de datos y Redis" "ambas responden"
  fi
else
  registrar MAL "base de datos y Redis" "la respuesta no trae la señal de dependencias"
fi

# ── Evidencia ───────────────────────────────────────────────────────────────
echo
if [[ $fallos -eq 0 ]]; then
  veredicto="DESPLIEGUE VERIFICADO"
else
  veredicto="DESPLIEGUE NO VERIFICADO ($fallos comprobaciones fallidas)"
fi
echo "$veredicto"

if [[ "${SIN_EVIDENCIA:-0}" != "1" ]]; then
  raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  destino="$raiz/docs/infra/operacion/despliegues"
  mkdir -p "$destino"
  archivo="$destino/$(date -u +%Y-%m-%d)-${COMMIT_ESPERADO:-sin-sha}.txt"
  {
    echo "Smoke test posterior al despliegue"
    echo "API:     $API"
    echo "Momento: $inicio"
    echo "Commit:  ${COMMIT_ESPERADO:-no declarado}"
    echo
    printf '%s' "$salida"
    echo
    echo "$veredicto"
  } > "$archivo"
  echo "Evidencia: ${archivo#"$raiz"/}"
fi

exit $(( fallos > 0 ? 1 : 0 ))
