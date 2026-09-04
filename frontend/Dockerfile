# syntax=docker/dockerfile:1

# La version de Node y el gestor de paquetes son UNO SOLO en todo el proyecto
# (spec 14, criterios 5 y 6): node 22 y el pnpm que declara `packageManager` en
# package.json, que corepack instala aqui y que CI usa por la misma via.
# `scripts/verificar_paridad.py` falla la construccion si esto se desalinea.
#
# Hasta la spec 14 esta imagen se construia con `npm ci` sobre un
# package-lock.json de un mes antes mientras CI validaba pnpm sobre otro
# lockfile: el artefacto que llegaba a produccion no era el que CI habia
# probado. El package-lock.json quedo eliminado.
ARG NODE_VERSION=22

# ── Etapa 1: deps ───────────────────────────────────────────────────────────
# Instala node_modules (incluye devDependencies, necesarias para el build).
FROM node:${NODE_VERSION}-alpine AS deps
RUN apk add --no-cache libc6-compat
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
# --frozen-lockfile falla si el lockfile no corresponde al manifiesto: es lo que
# hace que "las dependencias estan bloqueadas" sea comprobable y no una promesa.
RUN pnpm install --frozen-lockfile

# ── Etapa 2: builder ────────────────────────────────────────────────────────
# Compila la app. Las NEXT_PUBLIC_* se inlinean en el bundle del cliente durante
# el build, por eso llegan como build args (ver docker-compose / build-push.sh)
# y no como variables de entorno de runtime.
FROM node:${NODE_VERSION}-alpine AS builder
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_BASE_DOMAIN=localhost
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000
ARG NEXT_PUBLIC_SINGLE_HOST_MODE=false
# Spec 14, criterio 8: el commit del que sale este bundle. Va aqui y no en la
# etapa final porque las NEXT_PUBLIC_* se inlinan durante el build; puesta como
# variable de runtime no llegaria al navegador.
ARG GIT_SHA=desconocido
ENV NEXT_PUBLIC_GIT_SHA=$GIT_SHA \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_BASE_DOMAIN=$NEXT_PUBLIC_BASE_DOMAIN \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_PUBLIC_SINGLE_HOST_MODE=$NEXT_PUBLIC_SINGLE_HOST_MODE \
    NEXT_TELEMETRY_DISABLED=1

# Genera .next/standalone (server.js + node_modules mínimos) gracias a
# output: "standalone" en next.config.ts.
RUN pnpm run build

# ── Etapa 3: runner ─────────────────────────────────────────────────────────
# Imagen final mínima: solo el output standalone + assets estáticos. Sin el
# node_modules completo ni el código fuente. ~250 MB en lugar de ~1.17 GB.
FROM node:${NODE_VERSION}-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Usuario sin privilegios.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# El servidor standalone NO copia public/ ni .next/static por defecto: se
# añaden a mano (ver docs de output).
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# server.js lo genera el output standalone; reemplaza a `next start`.
CMD ["node", "server.js"]
