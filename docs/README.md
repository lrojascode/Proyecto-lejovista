# LejoVista — Documentación Técnica

> Plataforma SaaS multi-tenant para el **análisis y la optimización autónoma de campañas de Meta Ads con Inteligencia Artificial**.

Esta documentación describe el estado **real** del repositorio, inferido íntegramente del código fuente. Cuando existe una contradicción entre esta documentación y el código, **el código tiene prioridad**; en ese caso, abrir un issue para corregir el documento.

---

## ¿Qué es LejoVista?

LejoVista conecta las cuentas publicitarias de Meta (Facebook/Instagram Ads) de un cliente, **sincroniza** diariamente sus métricas, las **analiza** con un motor de IA en tres capas (reglas → estadística → LLM), **propone acciones** de optimización (pausar, ajustar presupuesto, cambiar placements, crear campañas) y, con aprobación humana, las **ejecuta** contra la Graph API de Meta. Además genera un **Master Brief** narrativo, reportes en PDF y aprende de los resultados reales de las campañas que la propia IA crea.

El producto está calibrado hoy para el **mercado peruano** (moneda `PEN`, región Lima) y campañas de **mensajería a WhatsApp**, aunque gran parte de esos valores están centralizados y son parametrizables (ver [10-configuracion](10-configuracion.md) y [16-decisiones-arquitectonicas](16-decisiones-arquitectonicas.md)).

## Stack detectado (resumen)

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.11, **FastAPI**, SQLAlchemy 2.0 async, Pydantic v2 |
| Workers | **Celery 5** + **Redis** (broker/result/pubsub), Celery Beat |
| Base de datos | **PostgreSQL 16** (asyncpg), migraciones con **Alembic** |
| Frontend | **Next.js 16** (App Router), **React 19**, TypeScript, Recharts |
| IA / LLM | OpenAI · Gemini (directo) · OpenRouter, **elegidos explícitamente por Organización** (spec 10; las claves de entorno ya no eligen proveedor). Modelos y precios en el catálogo `ai_model_catalog` |
| Integración externa | **Meta Graph/Marketing API** `v26.0` |
| PDF | WeasyPrint (HTML→PDF server-side) |
| Infra | **Docker Compose**, **Nginx** (reverse proxy multi-tenant por subdominio), imágenes en GHCR |
| Observabilidad | Sentry (opcional), logging estructurado, healthcheck profundo |

Detalle completo en [01-arquitectura-general](01-arquitectura-general.md).

## Puesta en marcha rápida (desarrollo local)

```bash
# 1. Copiar variables de entorno y generar secretos
cp .env.example .env
#   SECRET_KEY:     openssl rand -hex 64
#   ENCRYPTION_KEY: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# En local dejar MOCK_SERVICES=true (no requiere credenciales de Meta ni de IA).

# 2. Levantar todo el stack (db, redis, backend, 3 workers, beat, frontend, nginx)
# El servicio `migrate` aplica `alembic upgrade head` automáticamente antes
# de que `backend` arranque (O-2) — ya no hace falta correrlo a mano.
docker compose up -d

# 3. (Opcional) Sembrar un tenant de prueba
docker compose exec backend python scripts/seed_tenant.py --org "Demo" --token xxx --account-id act_123 --page-id 111

# 4. Abrir la app
#    http://localhost:3000  (landing)   ·   http://cliente1.localhost  (tenant)
#    http://api.localhost/docs          (OpenAPI, si ENABLE_API_DOCS=true)
```

Guía completa en [14-deployment](14-deployment.md) y [10-configuracion](10-configuracion.md).

## Índice de la documentación

Ver [INDEX.md](INDEX.md) para el mapa completo y el orden de lectura recomendado.

## Convención de referencias del código

A lo largo del repositorio abundan marcadores como `AI-1`, `AI-2`, `C-3`, `D-2`, `S-8`, `F-2`, `Bug 5`, `Ajuste 2A`. Son identificadores de **hallazgos de auditoría** y **fases de plan** documentados en `HANDOFF.md` y `plan-implementacion-auditoria.md` (raíz del repo). Esta documentación los reutiliza cuando aporta trazabilidad. Ver el [glosario](17-glosario.md) para su significado.
