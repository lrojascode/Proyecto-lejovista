# Handoff — Remediación auditoría 2026

Estado al 2026-07-03. Todo commiteado en `main`. La referencia de hallazgos es [auditoria2026.md](auditoria2026.md).

## ✅ Hecho (con tests verdes)

Suite: **28 tests pasan** en Docker (`backend/run_tests.sh`). No requiere Postgres/Redis (SQLite en archivo + Celery mockeado).

| Fase | Commit | Hallazgos cerrados |
|------|--------|--------------------|
| (previa) | `fca0ae9` | S-1, S-2, S-3, S-7, A-1, D-1/D-2, F-1 |
| 1 | `2fc6802` | A-2 (reintentos/backoff), AI-6/S-10 (flag `ENABLE_META_WRITEBACK`), I-1 (Beat cron + jitter) |
| 2 | `79223d4` | AI-1 (sin cifras inventadas), AI-2 (presupuesto real), AI-3 (histórico insuficiente), AI-4 (PROMPT_VERSION), AI-5 (umbrales centralizados) |
| 3 | `50390f9` | D-4 (metadatos narrativa), D-5 (create_all gateado, Alembic), D-6 (ondelete FKs) |
| 4 | `b16f537` | S-4 (sin anti-detección), S-6 (expiración tokens), S-10 (cliente Meta real) |
| 5 | `04ecac3` | I-3 (healthchecks/limits/nginx), I-4 (logging+Sentry), I-5 (cooldown triggers) |
| 6 | `8a1adf5` | P-1 (aprobador honesto), F-2 (estados de error), F-3 (aria-live) |
| 7 | `bea739d` | A-6 (testing) + infra de test + bug real de signup (`MissingGreenlet`) |
| 8 | `f908e55` | A-5 (loop limpio en Celery), A-4 (reportes de solo lectura) |
| 9 | `bee3730` | S-8 (organización activa real en multi-tenant) |
| 10 | `7de957f` | S-9 (refresh de JWT + revocación / logout) |
| 11 | `77a4013` | P-2 (slice): breakdowns género/dispositivo/geografía |

## ⏳ Falta (esfuerzo L / arquitectura — retomar acá)

Lo que queda son refactors grandes que el propio roadmap del audit ubicó en "próximo trimestre". No tocarlos en caliente sin plan.

1. **P-2 — Resto de la captura de reporting (L).** Ya se capturan género/dispositivo/geografía a nivel campaña (fase 11). Falta lo pesado: **nivel adset y ad/creativo, inventario de creativos, audiencias personalizadas**, conectar `meta_sync_service.generate_sync_plan` (stub) a persistencia real, y **renderizar** todo en el frontend / `brief-data`. Prerrequisito del generador de PDF.
2. **A-3 — Consolidar los dos stacks de Meta (L).** Conviven `services/meta_ads/*` (flujo real) y Stack B (`meta_api_client.py` + `rate_limiter.py` + `meta_sync_service.py` + `master_brief_generator.py`, mayormente stub). Elegir uno (recomendado `meta_api_client.py`), migrar `sync.py`/`campaigns.create`, borrar el otro, unificar credenciales.
3. **AI-2 (cierre) — Resolver adset real antes del write-back.** El presupuesto vive a nivel adset; hoy se apunta a la campaña y `target_adset_id` no se resuelve. Depende de la captura de adsets (punto 1). **Mantener `ENABLE_META_WRITEBACK=false` hasta cerrarlo.**
4. **D-3 — Particionado/archivado de `campaign_snapshots` (M/L, ops).** Particiones por rango de `date` (mensual) + archivado > 12-18 meses.
5. **F-4 — Unificar lenguaje visual (M, pulido).** Dashboard en Tailwind claro vs. resto glass oscuro. Unificar en design tokens/componentes.

### Bugs de infra menores (no auditoría)
- `frontend/__tests__/page.test.tsx` falla por resolución de CSS modules en jest (preexistente, ajeno a la auditoría). Fix: `moduleNameMapper` para `\.css$` en `jest.config.ts`. Los tests de login y diagnostics pasan.

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
