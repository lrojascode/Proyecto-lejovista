# Índice de la Documentación — LejoVista

Documentación técnica generada a partir del análisis directo del código fuente. Orden de lectura sugerido de arriba hacia abajo, aunque cada documento es autocontenido.

## Mapa de documentos

| # | Documento | Contenido |
|---|-----------|-----------|
| — | [README](README.md) | Qué es LejoVista, stack, arranque rápido |
| — | [INDEX](INDEX.md) | Este índice |
| 01 | [Arquitectura general](01-arquitectura-general.md) | Visión de alto nivel, componentes, diagramas de sistema |
| 02 | [Estructura del repositorio](02-estructura-del-repositorio.md) | Árbol de directorios y responsabilidad de cada carpeta |
| 03 | [Backend](03-backend.md) | FastAPI, capas, servicios, patrones, workers |
| 04 | [Frontend](04-frontend.md) | Next.js App Router, providers, cliente API, multi-tenant |
| 05 | [Base de datos](05-base-de-datos.md) | PostgreSQL, esquema, índices, migraciones Alembic, archivado |
| 06 | [API](06-api.md) | Referencia de endpoints REST y WebSocket |
| 07 | [Modelos](07-modelos.md) | Modelos SQLAlchemy y relaciones (ERD) |
| 08 | [Autenticación](08-autenticacion.md) | JWT, refresh tokens, multi-tenant, WebSocket auth |
| 09 | [Seguridad](09-seguridad.md) | Cifrado, aislamiento de tenants, write-back gating, rate limiting |
| 10 | [Configuración](10-configuracion.md) | Variables de entorno y feature flags |
| 11 | [Servicios externos](11-servicios-externos.md) | Meta Graph API, LLMs, Redis, Sentry |
| 11b | [Inventario de la API de Meta](11-inventario-meta.md) | Endpoints, campos, permisos y comportamientos de los que depende el producto (spec 08) |
| 12 | [Flujos](12-flujos.md) | Ciclo de vida de peticiones, sync, análisis, creación de campañas |
| 13 | [Testing](13-testing.md) | Estrategia de pruebas, fixtures, cobertura |
| 14 | [Deployment](14-deployment.md) | Docker Compose, build-push, Nginx, túnel, healthchecks |
| 15 | [Deuda técnica](15-deuda-tecnica.md) | Riesgos, código muerto, mejoras propuestas (con gravedad) |
| 16 | [Decisiones arquitectónicas](16-decisiones-arquitectonicas.md) | ADRs implícitas y su justificación |
| 17 | [Glosario](17-glosario.md) | Términos de dominio, códigos de auditoría, siglas |
| 18 | [Motor de IA](18-motor-ia.md) | Detalle profundo de las 3 capas, planner, executor, aprendizaje |
| 19 | [Sincronización con Meta](19-sincronizacion-meta.md) | Pipeline de sync, rate limiting, mock vs real |
| 20 | [Creación de campañas (Saga)](20-creacion-campanas.md) | Orquestación por pasos, sugeridor, validador, targeting, UX del formulario |
| 21 | [Producto — datos visualizados](21-producto-datos-visualizados.md) | Qué dato ve el cliente, dónde y por qué: dashboard, lista y reporte; brechas de producto |
| 22 | [Plan de remediación de deuda](22-plan-remediacion-deuda.md) | Plan de trabajo secuenciado (sprints) para cerrar los hallazgos de 15-deuda-tecnica |
| 23 | [Chat de creación de campañas](23-chat-creacion-campanas.md) | Segunda puerta de entrada conversacional al mismo motor: máquina de estados, extracción de slots, compuertas de revisión/riesgo |

## Cómo se mantiene esta documentación

- Es un **snapshot** del código al momento de generarse. Los diagramas Mermaid y las tablas de endpoints/modelos deberían regenerarse cuando cambien `app/api/v1/*`, `app/models/*` o `app/services/*`.
- Los documentos con mayor riesgo de quedar obsoletos: [06-api](06-api.md), [07-modelos](07-modelos.md), [10-configuracion](10-configuracion.md). Ver la sección "Mantenimiento automático sugerido" al final de [16-decisiones-arquitectonicas](16-decisiones-arquitectonicas.md).

## Fuentes de verdad complementarias en el repo (no generadas aquí)

Estos documentos existían antes en la raíz y contienen contexto de negocio/histórico valioso, pero **no siempre reflejan el estado actual del código** (por eso esta carpeta `docs/` los reinterpreta):

- `HANDOFF.md` — bitácora de la auditoría y hallazgos (códigos `A-*`, `B-*`, `C-*`, `D-*`, `S-*`, `I-*`, `AI-*`, `F-*`).
- `plan-implementacion-auditoria.md` — plan por fases de la auditoría.
- `PLAYBOOK-META-ADS-IA.md` — reglas de negocio de Meta Ads (nomenclatura, placements, WhatsApp).
- `PLAN-CREACION-CAMPANAS.md` — diseño del flujo de creación de campañas.
