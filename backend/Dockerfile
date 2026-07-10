# syntax=docker/dockerfile:1

# ── Etapa 1: builder ────────────────────────────────────────────────────────
# Compila las dependencias (build-essential, headers) dentro de un venv aislado.
# Nada de esto queda en la imagen final.
FROM python:3.11-slim AS builder

# Herramientas de compilación solo para construir wheels (bcrypt/cffi, etc.).
# libpq-dev se mantiene por compatibilidad con la build actual; asyncpg no lo
# necesita en runtime, por eso no se instala en la etapa final.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# venv autocontenido en /opt/venv: se copia entero a la imagen final.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Etapa 2: runtime ────────────────────────────────────────────────────────
# Imagen final: python slim + el venv ya construido. Sin compilador, sin curl,
# sin headers de desarrollo. ~400 MB en lugar de ~890 MB.
FROM python:3.11-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Solo las librerías de sistema que el runtime necesita de verdad.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

EXPOSE 8000

# CMD apto para producción (sin --reload). El compose local sobreescribe este
# comando para añadir --reload durante el desarrollo.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
