FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema necesarias para construir ciertos paquetes de Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar archivos de requerimientos primero para caché de capas
COPY requirements.txt .

# Instalar dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del backend
COPY . .

# Exponer el puerto de FastAPI
EXPOSE 8000

# Comando por defecto para correr FastAPI con recarga automática
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
