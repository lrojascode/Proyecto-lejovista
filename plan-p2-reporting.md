# Plan P-2 — Reporting completo (nivel adset / anuncio / creativo)

Objetivo: que `GET /reports/brief-data` + el frontend reproduzcan las secciones del
`informe-rendimiento-jun2026.html` con datos reales de Meta, no solo a nivel campaña.

## Brecha (informe HTML vs. app hoy)

| Sección | Hoy | Falta |
|---|---|---|
| KPIs (invertido, alcance, conversaciones, mejor costo/conv, CTR) | Parcial | `reach`, `conversations` |
| Alertas rojo/verde | ✅ insights | — |
| Ranking campañas por CPL | ✅ | — |
| Detalle por adset | ❌ | modelo + tabla |
| Detalle por anuncio/creativo | ❌ | modelo + tabla |
| Tendencia semanal + causas | Parcial | agregación semanal |
| Edad/género, placement, dispositivo, geo | Parcial (mock) | capturar en sync real |
| Audiencias personalizadas | ❌ | modelo + sync |
| Inventario de creativos | ❌ | modelo + sync |
| Parámetros óptimos | ❌ | heurística |

## Decisiones de diseño
1. Extender el Stack A (`services/meta_ads/sync.py`, el que funciona) reutilizando la
   lista de queries ya escrita en `services/meta_sync_service.py::generate_sync_plan`.
   La consolidación total de stacks (A-3) queda para después.
2. Reusar el scaffold de `services/master_brief_generator.py` (ya tiene el dict con
   todas las secciones) como agregador del brief.
3. Paridad mock: todo lo real tiene su versión en `_sync_mock_data` para ver el
   dashboard completo en local sin token de Meta.

## Fases (cada una = 1 commit con tests)

- **P-2.1 — Modelo de datos (M).** `reach`/`conversations` en campaign_snapshot;
  nuevos modelos `adset_snapshot`, `ad_snapshot`, `meta_creative`, `custom_audience`;
  migración Alembic; FKs con ondelete CASCADE.
- **P-2.3 — Paridad mock (M).** `_sync_mock_data` genera adsets/ads/creativos/
  audiencias coherentes → dashboard completo en local.
- **P-2.2 — Sync real (L).** Extender `_sync_real_data` con las queries de adsets/ads/
  breakdowns/adimages/customaudiences; persistir (upsert); respetar rate limit.
- **P-2.4 — Agregador del brief (M/L).** Completar master_brief_generator: KPIs,
  ranking, tabla adsets, tabla ads/creativos, tendencia, segmentaciones, audiencias,
  inventario, parámetros óptimos (heurística). La narrativa LLM envuelve datos ya
  calculados en Python (no inventa cifras).
- **P-2.5 — API (S/M).** `GET /reports/brief-data` devuelve el brief completo;
  endpoints de detalle `/campaigns/{id}/adsets|ads` si hacen falta. Filtrado por tenant.
- **P-2.6 — Frontend (L).** Render de las secciones estilo informe HTML en /diagnostics
  o página nueva. Avanza también F-4 (unificación visual).

## Orden de ejecución
P-2.1 → P-2.3 (ya se ve todo en local) → P-2.2 → P-2.4 → P-2.5 → P-2.6.

## Beneficio colateral
Capturar adsets (P-2.1/2.2) permite cerrar AI-2 (resolver target_adset_id real) y
habilitar el write-back de presupuesto seguro.

## Fuera de alcance
Export PDF (otro prompt), consolidación de stacks A-3 (paralelo), particionado D-3.

## Riesgos
- Cuota de Meta: P-2.2 multiplica llamadas por cuenta (~13 queries). Probar con token real.
- Volumen de datos: más tablas diarias → refuerza necesidad futura de D-3 (archivado).

## Progreso
- [x] P-2.1 — modelo de datos (reach/conversations + adset_snapshot, ad_snapshot, meta_creative, custom_audience)
- [x] P-2.3 — paridad mock (adsets/ads/creativos/audiencias)
- [x] P-2.2 — sync real (campaña con reach/conversations + adsets/ads/breakdowns/adimages/customaudiences)
- [x] P-2.4 — agregador del brief (ReportBuilder)
- [x] P-2.5 — API (report en /reports/brief-data)
- [x] P-2.6 — frontend (página /reporte con todas las secciones)
- [x] P-2.7 — extensión no planeada, hecha en sesión aparte (commits `b631c7b`, `7834b9f`, `efd57c2`):
      sync real captura breakdowns reales de Meta (placement/edad/género/dispositivo/región)
      via `_sync_real_breakdowns`; se distingue "sin audiencias" de "sin permiso" en logs/reporte;
      `ReportBuilder` genera hallazgos y recomendaciones en texto (derivados de datos, no del LLM);
      frontend con barras de distribución. **Sin tests** — pendiente cubrir en la próxima sesión.

## Pendiente real (no una fase nueva, es la validación de lo ya construido)
Todo P-2.1-2.7 está implementado y pasa tests con datos fake/mock, pero **nunca se
corrió un sync real contra la Graph API de Meta con un token vigente** — el único
intento real falló por token expirado antes de sincronizar. Hasta no confirmar esto
contra Meta real, P-2 sigue siendo "código listo, sin validar en producción".
