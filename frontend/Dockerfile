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

# Deshabilitar telemetría de Next.js
ENV NEXT_TELEMETRY_DISABLED=1

EXPOSE 3000

# En desarrollo local usamos npm run dev para contar con hot reload
CMD ["npm", "run", "dev"]
