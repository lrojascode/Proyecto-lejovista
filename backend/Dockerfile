# syntax=docker/dockerfile:1

# ── Etapa 1: builder ────────────────────────────────────────────────────────
# Compila las dependencias (build-essential, headers) dentro de un venv aislado.
# Nada de esto queda en la imagen final.
FROM python:3.11-slim AS builder

# Herramientas de compilación solo para construir wheels (bcrypt/cffi, etc.).
# libpq-dev se mantiene por compatibilidad con la build actual; asyncpg no lo
# necesita en runtime, por eso no se instala en la etapa final.
# libpango-1.0-0/libcairo2/libgdk-pixbuf-2.0-0 (5.3, F-3.2): headers de
# WeasyPrint (HTML→PDF del reporte) para que compile su binding cffi contra
# las librerías correctas.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
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
# WeasyPrint (5.3, F-3.2) las necesita en runtime, no solo al compilar —
# a diferencia de bcrypt/asyncpg, no son solo headers de compilación.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

EXPOSE 8000

# CMD apto para producción (sin --reload). El compose local sobreescribe este
# comando para añadir --reload durante el desarrollo.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
