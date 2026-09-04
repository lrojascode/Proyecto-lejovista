#!/usr/bin/env bash
# Corre la suite de tests del backend dentro de la imagen Docker (Python 3.11 con
# todas las dependencias). Por defecto no requiere Postgres ni Redis: los tests
# usan SQLite en archivo y mockean Celery. Las variables de entorno tienen
# defaults en conftest.py.
#
# Uso:
#   ./run_tests.sh                 # toda la suite, motor embebido
#   ./run_tests.sh tests/test_audit_fixes.py -v   # un archivo / flags extra
#
# Contra el motor de PRODUCCION (spec 14), que es donde el dialecto, las claves
# foraneas y las carreras se comportan como en produccion:
#
#   TEST_DATABASE_URL=postgresql+asyncpg://user@host:puerto/suite \
#   TEST_POSTGRES_URL=postgresql+asyncpg://user@host:puerto/concurrencia \
#   TEST_REDIS_URL=redis://host:puerto/9 \
#     ./run_tests.sh
#
# Las dos bases y el Redis son distintos a proposito: la suite recrea el esquema
# entre tests, las pruebas de concurrencia lo crean y destruyen por su cuenta, y
# el Redis se usa para probar que el mecanismo de sesiones falla cerrado cuando
# deja de responder. CI levanta los cuatro; ver .github/workflows/ci.yml.
#
# Las bases tienen que estar en UTF8, como produccion. En macOS, levantar un
# cluster de pruebas exige `LC_ALL=C initdb`, y eso las crea en SQL_ASCII: el
# primer texto con una raya o una comilla tipografica revienta con un error que
# no se parece a su causa. conftest.py lo comprueba al arrancar y lo dice.
#
#   createdb -E UTF8 -T template0 --lc-collate=C --lc-ctype=C suite
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
