# syntax=docker/dockerfile:1

# ── Etapa 1: deps ───────────────────────────────────────────────────────────
# Instala node_modules (incluye devDependencies, necesarias para el build).
FROM node:20-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── Etapa 2: builder ────────────────────────────────────────────────────────
# Compila la app. Las NEXT_PUBLIC_* se inlinean en el bundle del cliente durante
# el build, por eso llegan como build args (ver docker-compose / build-push.sh)
# y no como variables de entorno de runtime.
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_BASE_DOMAIN=localhost
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000
ARG NEXT_PUBLIC_SINGLE_HOST_MODE=false
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_BASE_DOMAIN=$NEXT_PUBLIC_BASE_DOMAIN \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_PUBLIC_SINGLE_HOST_MODE=$NEXT_PUBLIC_SINGLE_HOST_MODE \
    NEXT_TELEMETRY_DISABLED=1

# Genera .next/standalone (server.js + node_modules mínimos) gracias a
# output: "standalone" en next.config.ts.
RUN npm run build

# ── Etapa 3: runner ─────────────────────────────────────────────────────────
# Imagen final mínima: solo el output standalone + assets estáticos. Sin el
# node_modules completo ni el código fuente. ~250 MB en lugar de ~1.17 GB.
FROM node:20-alpine AS runner
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
