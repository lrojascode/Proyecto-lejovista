# Handoff — Remediación auditoría 2026

Estado al 2026-07-03. Todo commiteado en `main`. La referencia de hallazgos es [auditoria2026.md](auditoria2026.md).

## ✅ Hecho (con tests verdes)

Suite: **23 tests pasan** en Docker (`backend/run_tests.sh`). No requiere Postgres/Redis (SQLite en archivo + Celery mockeado).

| Fase | Commit | Hallazgos cerrados |
|------|--------|--------------------|
| (sesión previa) | `fca0ae9` | S-1 (claves fuera del repo), S-2 (auth en /sync), S-3 (auth WebSocket), S-7 (print DEBUG), A-1 (tareas Celery registradas), D-1/D-2 (unique+índice snapshots), F-1 (spinner sync) |
| 1 | `2fc6802` | A-2 (reintentos/backoff Celery), AI-6/S-10 (flag `ENABLE_META_WRITEBACK`), I-1 (Beat cron 6am + jitter) |
| 2 | `79223d4` | AI-1 (sin cifras inventadas), AI-2 (presupuesto real), AI-3 (aviso histórico insuficiente), AI-4 (PROMPT_VERSION), AI-5 (umbrales centralizados) |
| 3 | `50390f9` | D-4 (metadatos de narrativa), D-5 (create_all gateado, Alembic), D-6 (ondelete en FKs) |
| 4 | `b16f537` | S-4 (sin User-Agent falso / anti-detección), S-6 (expiración de tokens), S-10 (cliente Meta real) |
| 5 | `04ecac3` | I-3 (healthchecks/limits/nginx), I-4 (logging+Sentry opcional), I-5 (cooldown en triggers) |
| 6 | `8a1adf5` | P-1 (aprobador honesto simulado/real), F-2 (estados de error), F-3 (aria-live) |
| 7 | `bea739d` | A-6 (testing). Arreglos de infra de test + bug real de signup (`MissingGreenlet`) |

## ⏳ Falta (deuda de esfuerzo M/L — retomar acá)

Ordenado por prioridad sugerida:

1. **P-2 — Captura de datos de reporting (L).** Es la brecha de producto más grande. Hoy solo se persisten métricas a nivel campaña + placements + edad. Faltan **adset, ad/creativo, dispositivo, género, geografía, audiencias, inventario de creativos**. Ver la tabla del Anexo en `auditoria2026.md`. Requiere: nuevas tablas (`adset_snapshots`, `ad_snapshots`, breakdowns), conectar `meta_sync_service.generate_sync_plan` (hoy stub) a persistencia real, y exponerlo en `/reports/brief-data`. **Prerrequisito del generador de PDF.**
2. **A-3 — Consolidar los dos stacks de Meta (L).** Conviven `services/meta_ads/*` (usado por el flujo real) y `services/meta_api_client.py` + `rate_limiter.py` + `meta_sync_service.py` + `master_brief_generator.py` (Stack B, mayormente stub). Elegir uno (recomendado: `meta_api_client.py`), migrar `sync.py`/`campaigns.create`, borrar el otro. Unificar el modelo de credenciales.
3. **AI-2 (cierre) — Resolver adset real antes de habilitar write-back.** El presupuesto en Meta vive a nivel adset; hoy `planned_api_calls` apunta a la campaña y `target_adset_id` no se resuelve. Hasta cerrarlo, mantener `ENABLE_META_WRITEBACK=false`.
4. **A-4 — Sacar lógica de negocio de los controladores (M).** `reports.get_master_brief_data` aún genera y persiste narrativa dentro de un GET (efecto de escritura en lectura, hasta 45s). Mover a servicio + Celery; el GET solo lee `cached_narrative`.
5. **A-5 — `run_async` frágil en Celery (S/M).** `workers/tasks.py::run_async` reutiliza el event loop; migrar a `asyncio.run()` por tarea o `asgiref.async_to_sync`.
6. **S-8 / S-9 — Tenant activo real + refresh de JWT (M).** Hoy `organization_id` usa siempre `organizations[0]` (un usuario multi-org no ve la 2ª). Modelar "tenant activo" como claim del JWT. Añadir refresh token httpOnly + revocación (blocklist en Redis).
7. **D-3 — Particionado/archivado de `campaign_snapshots` (M/L).** Particiones por rango de `date` (mensual) + archivado > 12-18 meses. Es ops/infra.
8. **F-4 — Unificar lenguaje visual (M, pulido).** El dashboard usa Tailwind claro y el resto glass oscuro. Unificar en design tokens/componentes.

## 🚀 Cómo correr / verificar

```bash
# Tests del backend (Docker, sin DB/Redis):
cd backend && ./run_tests.sh              # 23 passed
./run_tests.sh tests/test_audit_fixes.py -v

# Tests del frontend:
cd frontend && npx jest

# Levantar el stack:
docker compose up -d --build
# Migraciones (prod usa esto; AUTO_CREATE_TABLES=false):
docker compose run --rm backend alembic upgrade head
```

## ⚠️ Notas de seguridad / operación

- **Claves rotadas** en `.env` (S-1). Al desplegar: todos re-loguean; cuentas de Meta conectadas con la clave vieja deben **reconectarse** (Fernet cambió).
- **`GEMINI_API_KEY` viva** en `.env` (gitignored pero real). **Rotarla en Google Cloud** — estuvo en texto plano.
- **`ENABLE_META_WRITEBACK=false`** por defecto: aprobar acciones simula, no escribe en Meta. No activar hasta cerrar AI-2 (punto 3).
- **`AUTO_CREATE_TABLES`**: `true` en dev local, `false` en prod (usar Alembic).
- Migraciones nuevas de esta tanda: `c3d1e5a9f7b2` (unique+índice snapshots) y `d5f2b1c8a3e4` (metadatos narrativa + FK ondelete).

## 📌 Workflow

Commits directos a `main`, sin PRs (`gh` no está instalado en el entorno).
