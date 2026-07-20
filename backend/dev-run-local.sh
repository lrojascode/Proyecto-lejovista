#!/usr/bin/env bash
# Arranca el backend contra Postgres/Redis locales (brew), no los hostnames
# de docker-compose del .env real. Solo para verificación manual en esta
# máquina — no se usa en CI ni en producción.
set -euo pipefail
cd "$(dirname "$0")"

export SECRET_KEY="dev-local-verification-secret-key-1234567890"
export ENCRYPTION_KEY="sHEU494awg7HtPuuIPg989QiYv0fUh0_kdmnGVa31N8="
export DATABASE_URL="postgresql+asyncpg://lejovista:localpassword123@localhost:5432/lejovista"
export REDIS_URL="redis://localhost:6379/0"
export CELERY_RESULT_BACKEND_URL="redis://localhost:6379/1"
export MOCK_SERVICES=true
export AUTO_CREATE_TABLES=false
export BASE_DOMAIN=localhost:3000
export ENABLE_API_DOCS=true

exec .venv/bin/uvicorn app.main:app --reload --port 8000
