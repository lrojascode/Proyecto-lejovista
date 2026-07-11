# 13 · Testing

## 1. Backend (pytest async)

- **Framework**: `pytest` + `pytest-asyncio` (`asyncio_mode = auto` en `pytest.ini`) + `pytest-mock`.
- **44 archivos de test** en `backend/tests/`.
- **Base de datos de test**: SQLite async en archivo temporal (`sqlite+aiosqlite`), con `NullPool` para evitar el binding de conexiones a un event loop (que causaba `MissingGreenlet`). Configurado en `conftest.py`.
- **Fixtures** (`conftest.py`):
  - `init_db` (autouse): crea y borra el esquema (`create_all`/`drop_all`) por test.
  - `db_session`: sesión de test.
  - `async_client`: `httpx.AsyncClient` con `ASGITransport` sobre la app real; `get_db` se sobreescribe con la sesión de test.
- **Entorno**: `conftest.py` fija `MOCK_SERVICES=true`, `ENABLE_META_WRITEBACK=false`, `AUTO_CREATE_TABLES=true` y claves de ejemplo **antes** de importar la app.
- **Ejecución**: `docker compose exec backend pytest` o `./run_tests.sh`.

### Áreas cubiertas (por nombre de archivo)

| Dominio | Tests representativos |
|---------|-----------------------|
| API/endpoints | `test_api_v1`, `test_main`, `test_kpis_summary_endpoint`, `test_recent_kpis_7d`, `test_campaign_creation_endpoint`, `test_campaign_suggestion_endpoint`, `test_pages_endpoint`, `test_spend_history` |
| Motor de IA | `test_classify_campaign`, `test_engine_brief_context`, `test_master_brief_v2`, `test_narrative_fallback`, `test_insight_dedupe`, `test_analysis_debounce` |
| Planner/executor | `test_planner_create_campaign`, `test_planner_modify_placements`, `test_executor_create_campaign`, `test_modifier_placements`, `test_writeback_allowlist` |
| Creación de campañas | `test_creation_tasks`, `test_campaign_validator`, `test_campaign_suggester`, `test_campaign_targeting`, `test_campaign_risk`, `test_campaign_drafts`, `test_campaign_image_upload`, `test_creative_builder`, `test_campaign_copy_llm` |
| Aprendizaje | `test_campaign_learning`, `test_campaign_outcome` |
| Sync | `test_sync_pages`, `test_sync_creative_copy` |
| Reporting | `test_report_builder_deltas`, `test_report_builder_labels`, `test_report_created_by_ai`, `test_report_pdf` |
| Infra/seguridad | `test_throttle`, `test_token_tracking`, `test_business_policies`, `test_audit_fixes`, `test_backup_state_persistence`, `test_ad_account_archive`, `test_workers`, `test_services` |

Los nombres reflejan que muchos tests nacieron para **fijar hallazgos de auditoría** (regresiones): dedupe de insights (`B-1`), debounce (`D-1`), persistencia de `backup_state` (`A-3`), allowlist de write-back (`F-2`), etc.

## 2. Frontend (Jest + Testing Library)

- **Framework**: Jest 30 + `@testing-library/react` + `jest-environment-jsdom`.
- **Config**: `jest.config.ts` + `jest.setup.ts` (matchers de `@testing-library/jest-dom`).
- **4 tests** en `frontend/__tests__/`: `login.test.tsx`, `page.test.tsx`, `diagnostics.test.tsx`, `learning.test.tsx`.
- **Ejecución**: `npm test`.

## 3. Estrategia y filosofía observada

- **Determinismo testeable**: las piezas de negocio (clasificador, veredicto de outcome, riesgo, targeting) son funciones puras testeadas como tabla de casos (`compute_verdict`, `classify_campaign`, `assess_campaign_risk`, `build_targeting`).
- **Mock por defecto**: con `MOCK_SERVICES=true` no se tocan Meta ni LLM; el sync mock genera datos realistas para probar el pipeline completo end-to-end sin credenciales.
- **Tests de regresión de auditoría**: cada bug corregido dejó un test que lo ancla.

## 4. Cobertura no automatizada / verificación manual

- No se detecta un umbral de cobertura configurado ni CI que lo imponga (ver [15-deuda-tecnica](15-deuda-tecnica.md) y [14-deployment](14-deployment.md)).
- La verificación de escrituras reales a Meta se hace manualmente con la cuenta piloto y el allowlist (`F-2`), no en tests automatizados (por diseño: escribir de verdad cuesta dinero).

## 5. Cómo escribir un test nuevo (backend)

```python
import pytest

@pytest.mark.asyncio
async def test_algo(async_client, db_session):
    # 1. Sembrar datos con db_session (modelos SQLAlchemy)
    # 2. Llamar al endpoint con async_client (headers de auth si aplica)
    # 3. Afirmar sobre la respuesta y/o el estado en DB
```

Para lógica pura, testear la función directamente sin fixtures de DB (ej. `compute_verdict`, `classify_campaign`).
