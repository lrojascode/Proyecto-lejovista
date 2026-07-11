# 19 · Sincronización con Meta

El pipeline de sync vive en `app/services/meta_ads/sync.py` (`MetaAdsSyncService`), disparado por la tarea Celery `sync_ad_account` (`workers/tasks.py`) y apoyado en `MetaAPIRateLimiter`.

## 1. Punto de entrada

`run_sync(ad_account, sync_type)`:
1. Crea un `SyncJob` `RUNNING`.
2. Según `MOCK_SERVICES`, llama a `_sync_mock_data` o `_sync_real_data`.
3. Marca `COMPLETED` (con `records_synced`) o, ante excepción, `FAILED`.
4. Clasifica el error: `TransientMetaError` se **re-lanza** para que Celery reintente (backoff); `PermanentMetaError` se registra como fallo definitivo (no reintenta).

La tarea `sync_ad_account` está decorada con `autoretry_for=(TransientMetaError,)`, `retry_backoff=True`, `retry_backoff_max=600`, `retry_jitter=True`, `max_retries=5`, `acks_late=True`.

## 2. Tipos de sync

| Tipo | Disparador | Ventana |
|------|-----------|---------|
| `FULL` | Conexión de una cuenta nueva | 30 días (`last_30d`) |
| `INCREMENTAL` | Trigger manual + Beat diario 06:00 | ayer (`yesterday`) |

## 3. Sync real (`_sync_real_data`)

Secuencia de llamadas a la Graph API (todas vía `call_api_paginated` salvo las puntuales):

```mermaid
flowchart TB
    A["1. GET /{act}\n(amount_spent, spend_cap, account_status)\n→ commit inmediato"] --> B["2. GET /{act}/campaigns\n(id, name, status, budgets)"]
    B --> C["3. GET /{act}/insights level=campaign\n(spend, impressions, reach, clicks, actions, frequency)\n→ upsert CampaignSnapshot"]
    C --> D["4. adsets (estructura+insights)\n→ AdsetSnapshot + budget map (Bug 5)"]
    D --> E["5. ads (estructura+insights+copy)\n→ AdSnapshot con creative_title/body/cta"]
    E --> F["6. inventario: adimages + customaudiences"]
    F --> G["7. páginas: /me/accounts + whatsapp_number por página"]
    G --> H["8. breakdowns por dimensión\n→ fusiona en CampaignSnapshot"]
    H --> I["9. histórico mensual de gasto\n(backfill único / mes en curso)"]
```

### Detalles relevantes

- **Commit inmediato** de la info de cuenta (paso 1): si algo más adelante falla, `spend_cap`/`amount_spent`/`status` ya quedan persistidos (antes se descartaban en silencio).
- **Upsert idempotente**: cada snapshot se busca por `(cuenta, entidad, fecha)` y se actualiza o inserta (protegido por `UniqueConstraint`).
- **Conversiones vs conversaciones** (`_extract_conversions_conversations`): `conversions` suma solo una **whitelist** de action types de lead (evita doble-conteo); `conversations` suma `messaging_conversation_started`, con fallback a leads si no hay mensajería.
- **Presupuesto ABO (`Bug 5`)**: si el `daily_budget`/`lifetime_budget` de campaña viene 0 (cuentas ABO), se usa la suma de los `daily_budget` de los adsets **ACTIVOS** de esa campaña.
- **Breakdowns** (`_sync_real_breakdowns`): 5 dimensiones (placement+posición, edad, género, dispositivo, región), cada una una llamada independiente; si Meta rechaza una (permiso o combinación no permitida) se registra y se continúa (no aborta el sync). Se fusionan en `placements_breakdown`/`demographics_breakdown`/`extra_breakdowns`.
- **Páginas + WhatsApp** (`_sync_real_pages`): `/me/accounts` (nivel usuario) descubre páginas; el `whatsapp_number` se pide por página y, si falla (falta permiso `whatsapp_business_management`), solo esa página se queda sin número.
- **Copy real de creativos**: `_sync_real_ads` captura `title`/`body`/`call_to_action_type` del creativo ganador para que la IA se inspire en el texto que funcionó (antes solo se guardaba la imagen).
- **Histórico mensual** (`_sync_spend_history`): backfill único con `time_increment=monthly&date_preset=maximum`; en syncs siguientes solo refresca el mes en curso y cierra perezosamente los meses viejos (`closed=True`).
- **Token expirado** (`_maybe_mark_expired`, `S-6`): ante `OAuthException code 190`, marca cuenta + credencial como `EXPIRED`.

## 4. Sync mock (`_sync_mock_data`)

Con `MOCK_SERVICES=true` genera datos realistas para probar todo el pipeline sin credenciales:
- 3 campañas de ejemplo con perfiles distintos: una **exitosa** (Reels barato), una **saturándose** (frecuencia creciente) y una **fallando** (0 leads los últimos días).
- 30 días de historia en `FULL`, 2 en `INCREMENTAL`; genera snapshots de campaña, adsets, anuncios, breakdowns completos, inventario de creativos/audiencias/página+WhatsApp y 18 meses de histórico de gasto.
- Simula las llamadas en el rate limiter para poblar `APICallLog`.

Los datos mock están calibrados para disparar exactamente las reglas de la Capa 1/2 (CPL crítico, saturación, oportunidad en Reels), lo que hace el sistema demostrable end-to-end.

## 5. Rate limiting (`rate_limiter.py`)

`MetaAPIRateLimiter` (ver también [11](11-servicios-externos.md)):
- **Backoff adaptativo** por la cabecera `x-business-use-case-usage`: espera `estimated_time_to_regain_access` si Meta lo indica; pausa preventiva de 15 s (>85%) o 3 s (>50%).
- **Pausas de cortesía** entre llamadas (0.2 s lecturas, 0.5 s escrituras; 0.05 s en mock).
- **Reintentos** ante 408/429/5xx (2 intentos, espera 60 s) y errores de red.
- **Paginación por cursor** (`call_api_paginated`), con parcial ante error tras datos acumulados.
- **Auditoría** en `APICallLog` con token enmascarado.

## 6. Post-sync

Al completar (`sync_ad_account`):
1. Notifica `sync_status` por WebSocket.
2. **Invalida** el cache del `ReportBuilder` de la cuenta.
3. **Agenda el análisis** de la organización con **debounce** (`D-1`): solo el primer sync de la org lo encola (lock Redis `SET NX EX`).

## 7. Programación (Beat)

`schedule_all_syncs("INCREMENTAL")` (06:00 Lima) itera todas las cuentas `ACTIVE` y encola cada una con `countdown=random(0, 1800)` (escalonado con jitter) para no golpear el rate limit de Meta con N cuentas a la vez.
