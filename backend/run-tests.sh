#!/usr/bin/env bash
# Suite de tests con el entorno AISLADO del stack de desarrollo.
#
# Correr `pytest` a secas dentro del contenedor hereda el .env del dev y falla
# ~70 tests por dos motivos que NO son bugs del código:
#
#   1. REDIS_URL apunta al Redis compartido, donde el rate limit de /auth/signup
#      (5 por hora por IP) ya está agotado por la app y por corridas previas =>
#      429 y ~60 tests caen con KeyError: 'access_token'. Se apunta a un puerto
#      muerto: `enforce_rate_limit` es fail-open a propósito, así que sin Redis
#      no limita (ver app/utils/throttle.py).
#   2. MOCK_SERVICES/WRITEBACK heredados hacen que los tests de sync peguen a la
#      API real de Meta con el token del cliente.
#
# La BD no hace falta aislarla: conftest.py usa SQLite en /tmp salvo que se fije
# TEST_DATABASE_URL, que la manda al motor de produccion (spec 14; ver
# run_tests.sh para las variables completas).
set -euo pipefail
docker compose exec -T \
  -e REDIS_URL=redis://127.0.0.1:6399/0 \
  -e MOCK_SERVICES=true \
  -e ENABLE_META_WRITEBACK=false \
  -e WRITEBACK_ACCOUNT_ALLOWLIST= \
  backend python -m pytest "${@:-tests/}"
