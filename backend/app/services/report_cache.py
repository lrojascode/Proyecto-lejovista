"""4.2 — Cache del ReportBuilder (B-2).

`ReportBuilder.build()` recorre snapshots de campaña/adset/ad y arma
segmentaciones — se llama por triplicado en el flujo de creación de campaña
(`/suggestion`, `/risk-preview` que se dispara al tipear, y `/create`), además
del Master Brief. Cachea el resultado serializado en Redis por
`(ad_account_id, window_days)` con TTL corto; se invalida explícitamente al
completar un sync de esa cuenta (`tasks.py::sync_ad_account`).

Fail-open como el resto de usos de Redis en el proyecto (`utils/throttle.py`):
si Redis no responde, se construye el reporte sin cache en vez de romper la
request.
"""
import json
import logging
from typing import Any, Dict

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ad_account import AdAccount
from app.services.report_builder import ReportBuilder

logger = logging.getLogger("lejovista.report_cache")

TTL_SECONDS = 600
_KEY_PREFIX = "report"


def _cache_key(ad_account_id: str, window_days: int) -> str:
    return f"{_KEY_PREFIX}:{ad_account_id}:{window_days}"


async def get_report(db: AsyncSession, ad_account: AdAccount, window_days: int = 30) -> Dict[str, Any]:
    key = _cache_key(ad_account.id, window_days)
    client = redis.Redis.from_url(settings.REDIS_URL)
    try:
        cached = await client.get(key)
        if cached is not None:
            return json.loads(cached)
    except Exception as e:
        logger.warning("Redis no disponible al leer cache de reporte (%s); se construye sin cache.", e)

    report = await ReportBuilder(db).build(ad_account, window_days=window_days)

    try:
        await client.set(key, json.dumps(report), ex=TTL_SECONDS)
    except Exception as e:
        logger.warning("Redis no disponible al guardar cache de reporte (%s).", e)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    return report


async def invalidate(ad_account_id: str) -> None:
    """Borra todas las ventanas cacheadas (7/30 días) de una cuenta —
    se llama tras completar un sync exitoso."""
    client = redis.Redis.from_url(settings.REDIS_URL)
    try:
        async for k in client.scan_iter(match=f"{_KEY_PREFIX}:{ad_account_id}:*"):
            await client.delete(k)
    except Exception as e:
        logger.warning("Redis no disponible al invalidar cache de reporte (%s).", e)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
