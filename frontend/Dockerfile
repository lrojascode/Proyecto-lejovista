FROM node:20-alpine

WORKDIR /app

# Instalar dependencias necesarias para compilar ciertos módulos de node
RUN apk add --no-cache libc6-compat

# Copiar archivos de paquete
COPY package*.json ./

# Instalar dependencias
RUN npm install

# Copiar el resto del código del frontend
COPY . .

# Las variables NEXT_PUBLIC_* se inlinean en el bundle del cliente durante el
# build, por eso llegan como build args (ver docker-compose.yml) y no sirven
# como variables de entorno en runtime.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_BASE_DOMAIN=localhost
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_BASE_DOMAIN=$NEXT_PUBLIC_BASE_DOMAIN \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_TELEMETRY_DISABLED=1

# Build de producción: el modo dev compilaba cada ruta bajo demanda dentro del
# contenedor (512 MB / 1 CPU) y el proceso moría por memoria.
RUN npm run build

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "start"]
