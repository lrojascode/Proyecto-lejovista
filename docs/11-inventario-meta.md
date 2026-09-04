# Inventario de la API de Meta

Qué endpoints, campos y permisos usa el producto (spec
[08](specs/08-meta-api-v26-y-gestion-de-deprecaciones.md)).

> **Para qué sirve.** Sin este documento, evaluar el impacto de una deprecación
> obliga a leer el código entero cada vez, y una migración de versión se
> convierte en descubrir errores en producción. Con él, leer un
> [changelog de Meta](https://developers.facebook.com/docs/graph-api/changelog)
> y responder «¿nos afecta?» es cuestión de minutos.
>
> **Se actualiza en el mismo cambio que introduce un uso nuevo.** Un inventario
> desactualizado es peor que ninguno: da falsa confianza.

**Versión en uso:** `v26.0` — fuente de verdad en
`backend/app/services/meta_ads/version.py`. Retirada prevista por Meta:
**2028-01-01**.

## Lecturas (sincronización)

| Endpoint | Campos que se piden | Dónde |
|---|---|---|
| `GET /{act_id}` | `amount_spent`, `spend_cap`, `account_status`, `currency` | `meta_ads/sync.py` |
| `GET /{act_id}/campaigns` | `id`, `name`, `status`, `effective_status`, `objective`, `created_time`, `start_time`, `stop_time`, `daily_budget`, `lifetime_budget` | `meta_ads/sync.py` |
| `GET /{act_id}/insights` | `spend`, `impressions`, `reach`, `clicks`, `actions`, `ctr`, `frequency` + `time_increment` | `meta_ads/sync.py` |
| `GET /{act_id}/adsets` | `id`, `name`, `campaign_id`, `status`, `effective_status`, `targeting`, `daily_budget`, `lifetime_budget`, `end_time` | `meta_ads/sync.py` |
| `GET /{act_id}/ads` | `id`, `name`, `adset_id`, `creative`, `status`, `effective_status` | `meta_ads/sync.py` |
| `GET /{act_id}/adcreatives` | `id`, `object_story_spec`, `title`, `body`, `call_to_action_type` | `meta_ads/sync.py` |
| `GET /me/accounts` | `id`, `name`, `access_token`, `instagram_business_account` | descubrimiento de páginas |
| `GET /debug_token` | `is_valid`, `expires_at`, `scopes`, `data_access_expires_at` | `meta_ads/token_inspection.py` |

### Desgloses (`breakdowns`)

`publisher_platform`, `platform_position`, `age`, `gender`, `device_platform`,
`region`. Un desglose retirado deja el reporte sin esa dimensión pero **no**
rompe el sync: se piden por separado.

## Escrituras (creación de campañas)

| Endpoint | Campos que se envían | Notas de comportamiento |
|---|---|---|
| `POST /{act_id}/campaigns` | `name`, `objective`, `buying_type`, `status`, `special_ad_categories`, `is_adset_budget_sharing_enabled` | `is_adset_budget_sharing_enabled` es **obligatorio** con presupuesto en el adset (error_subcode 4834011 si falta) |
| `POST /{act_id}/adsets` | `name`, `campaign_id`, `optimization_goal`, `destination_type`, `billing_event`, `bid_strategy`, `start_time`, `end_time`, `status`, `targeting`, `promoted_object`, `daily_budget` \| `lifetime_budget` | CTWA: `promoted_object` es **solo** `page_id`. `whatsapp_phone_number` no es válido aquí. `daily_budget` y `lifetime_budget` son mutuamente excluyentes |
| `POST /{act_id}/adcreatives` | `name`, `object_story_spec` | CTWA: `link` es **obligatorio** dentro de `link_data` |
| `POST /{act_id}/ads` | `name`, `adset_id`, `creative`, `status` | Siempre `PAUSED` (invariante, spec 03) |
| `POST /{act_id}/adimages` | multipart, campo con el nombre del archivo | **No admite WebP** (error_subcode 1487411): se convierte a PNG antes |
| `POST /{act_id}/advideos` | multipart, campo `source`, host `graph-video.facebook.com` | Host distinto del resto |
| `POST /{id}` (modificar) | `status`, `daily_budget`, `lifetime_budget`, `targeting` | Presupuestos en **centavos** enteros |
| `DELETE /{id}` | — | Solo en el rollback de una Saga fallida |

## Permisos requeridos

`ads_read`, `ads_management`, `business_management`, `pages_show_list`,
`pages_read_engagement`, `whatsapp_business_management`.

Su vigencia se comprueba en `debug_token`; la caducidad del acceso a datos
(`data_access_expires_at`) es distinta de la del token y se vigila aparte.

## Comportamientos de los que se depende

Cosas que no son campos y que una versión nueva puede cambiar. Cada una está
codificada en una prueba, que es lo que convierte este inventario en algo
verificable:

| Comportamiento | Prueba que lo fija |
|---|---|
| CTWA: `promoted_object` del adset es solo `page_id` | `test_creation_tasks.py` |
| CTWA: `link` obligatorio en `link_data` | `test_creative_builder.py` |
| Presupuestos en centavos enteros | `test_budget_type.py` |
| `daily_budget` y `lifetime_budget` mutuamente excluyentes | `test_budget_type.py` |
| Nombres de `instagram_positions` (`stream`, `explore_home`, `ig_search`) | `test_modifier_placements.py` |
| `x-business-use-case-usage` para el backoff | `test_compatibilidad_parametros.py` |
| `issues_info` tras crear (una campaña aceptada puede no entregar) | `test_creation_tasks.py` |

## Migrar a una versión nueva

1. Actualizar `VERSION_SOPORTADA` y su fecha de expiración en
   `meta_ads/version.py`, y añadir la nueva a `VERSIONES_ADMITIDAS` **sin quitar
   la anterior** — el rollback tiene que ser cambiar un campo, no desplegar.
2. Alinear el resto de sitios y comprobar con
   `python backend/scripts/verificar_version_meta.py`.
3. Activar la candidata **solo en la cuenta piloto**
   (`Organization.meta_api_version_candidata`).
4. Ejercitarla de verdad: un **sync completo** y una **creación pausada**. Los
   campos retirados se manifiestan en el sync; los cambios de contrato de
   escritura, solo al escribir. Una llamada de humo no encuentra ninguna de las
   dos cosas.
5. Revisar este inventario contra el changelog y actualizarlo.
6. Extender al resto de organizaciones.

**Rollback:** vaciar `meta_api_version_candidata`. Sin construir ni publicar
imagen.
