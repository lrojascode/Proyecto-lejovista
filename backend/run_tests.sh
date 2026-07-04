#!/usr/bin/env bash
# Corre la suite de tests del backend dentro de la imagen Docker (Python 3.11 con
# todas las dependencias). No requiere Postgres ni Redis: los tests usan SQLite
# en archivo y mockean Celery. Las variables de entorno tienen defaults en conftest.py.
#
# Uso:
#   ./run_tests.sh                 # toda la suite
#   ./run_tests.sh tests/test_audit_fixes.py -v   # un archivo / flags extra
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="lejovista-backend-test"

# Construir la imagen si no existe (cachea deps; reconstruir tras cambiar requirements).
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Construyendo imagen de test $IMAGE ..."
  docker build -t "$IMAGE" .
fi

# Montar el código actual para no reconstruir en cada cambio.
docker run --rm \
  -v "$(pwd)":/app \
  "$IMAGE" \
  pytest "${@:-}"
