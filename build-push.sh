#!/usr/bin/env bash
#
# Construye y publica las imágenes de producción en el registry, desde tu Mac.
# Fallback manual del pipeline: normalmente esto lo hace GitHub Actions
# (.github/workflows/deploy.yml). Úsalo si Actions está caído. El deploy al VPS
# lo hace Coolify (panel → recurso lejovista → Redeploy); ver docs/infra/deploy-runbook.md.
#
# Uso:
#   BASE_DOMAIN=tudominio.com ./build-push.sh
#
# La etiqueta por defecto es el sha del commit, no `latest` (spec 14, criterio
# 7): lo que se despliega tiene que ser identificable, y `latest` no lo es. Para
# publicar además la flotante: IMAGE_TAG=latest ./build-push.sh
#
# Variables (con sus defaults):
#   IMAGE_PREFIX  ghcr.io/lrojascode   prefijo del registry (GHCR o Docker Hub)
#   IMAGE_TAG     el sha del commit    tag de las imágenes
#   PLATFORM      linux/amd64          arquitectura del VPS (NO la de tu Mac arm64)
#   BASE_DOMAIN   (obligatoria)        dominio de prod; define las NEXT_PUBLIC_*
#
# Requisitos previos (una vez):
#   - docker buildx (viene con Docker Desktop)
#   - login al registry:  echo $TOKEN | docker login ghcr.io -u lrojascode --password-stdin
#
set -euo pipefail

IMAGE_PREFIX="${IMAGE_PREFIX:-ghcr.io/lrojascode}"
GIT_SHA="$(git rev-parse HEAD)"
IMAGE_TAG="${IMAGE_TAG:-$GIT_SHA}"

# Construir desde un arbol sucio publica una imagen etiquetada con un commit que
# no la describe, y entonces `/version` miente. Es lo unico que hace inservible
# el identificador, asi que se corta aqui.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "El arbol tiene cambios sin confirmar: la imagen quedaria etiquetada con" >&2
  echo "$GIT_SHA sin ser ese codigo. Confirma o descarta antes de publicar." >&2
  exit 1
fi
PLATFORM="${PLATFORM:-linux/amd64}"
BASE_DOMAIN="${BASE_DOMAIN:?Define BASE_DOMAIN, p.ej. BASE_DOMAIN=tudominio.com ./build-push.sh}"

BACKEND_IMAGE="${IMAGE_PREFIX}/proyectolejovista-backend:${IMAGE_TAG}"
FRONTEND_IMAGE="${IMAGE_PREFIX}/proyectolejovista-frontend:${IMAGE_TAG}"

echo "→ Registry:  ${IMAGE_PREFIX}"
echo "→ Commit:    ${GIT_SHA}"
echo "→ Tag:       ${IMAGE_TAG}"
echo "→ Platform:  ${PLATFORM}"
echo "→ Dominio:   ${BASE_DOMAIN}"
echo

# Un builder de buildx capaz de compilar cross-platform y hacer push directo.
docker buildx inspect lejovista-builder >/dev/null 2>&1 || \
  docker buildx create --name lejovista-builder --driver docker-container >/dev/null
docker buildx use lejovista-builder

echo "══ Backend (imagen única para backend + workers + beat) ══"
docker buildx build \
  --platform "${PLATFORM}" \
  --build-arg GIT_SHA="${GIT_SHA}" \
  -t "${BACKEND_IMAGE}" \
  --push \
  ./backend

echo "══ Frontend (NEXT_PUBLIC_* inlinadas con el dominio de prod) ══"
docker buildx build \
  --platform "${PLATFORM}" \
  --build-arg GIT_SHA="${GIT_SHA}" \
  --build-arg NEXT_PUBLIC_API_URL="https://api.${BASE_DOMAIN}" \
  --build-arg NEXT_PUBLIC_BASE_DOMAIN="${BASE_DOMAIN}" \
  --build-arg NEXT_PUBLIC_WS_URL="wss://api.${BASE_DOMAIN}" \
  --build-arg NEXT_PUBLIC_SINGLE_HOST_MODE="false" \
  -t "${FRONTEND_IMAGE}" \
  --push \
  ./frontend

echo
echo "✔ Publicado:"
echo "   ${BACKEND_IMAGE}"
echo "   ${FRONTEND_IMAGE}"
echo
echo "Para desplegar: Coolify → recurso lejovista → IMAGE_TAG=${IMAGE_TAG} → Redeploy"
echo "(Coolify pullea estas imágenes, corre 'migrate' y reinicia el stack)."
echo
echo "Y despues, que es parte del despliegue:"
echo "  COMMIT_ESPERADO=${GIT_SHA} SMOKE_EMAIL=... SMOKE_PASSWORD=... \\"
echo "    scripts/despliegue/smoke.sh https://api.${BASE_DOMAIN}"
