"""Rate-limit propio (cooldown) para endpoints costosos.

Protege /analyze-trigger y /sync-trigger de spam que dispararía costo de LLM y
llamadas a Meta. Usa Redis con SET NX + expiración. Es *fail-open*: si Redis no
está disponible, se permite la acción (no bloqueamos la app por un fallo del
limitador) y se registra una advertencia.
"""
import logging
import redis.asyncio as redis
from fastapi import HTTPException, status
from app.config import settings

logger = logging.getLogger("lejovista.throttle")


async def enforce_cooldown(action: str, org_id: str, cooldown_seconds: int) -> None:
    """Lanza HTTP 429 si `action` para `org_id` se ejecutó hace menos de
    `cooldown_seconds`. Fail-open ante errores de Redis."""
    if not org_id:
        return

    key = f"cooldown:{action}:{org_id}"
    client = redis.Redis.from_url(settings.REDIS_URL)
    try:
        was_set = await client.set(key, "1", nx=True, ex=cooldown_seconds)
        if not was_set:
            ttl = await client.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Acción '{action}' ejecutada recientemente. Reintenta en ~{max(ttl, 1)}s.",
            )
    except HTTPException:
        raise
    except Exception as e:  # ConnectionError, etc. => fail-open
        logger.warning("Rate limiter no disponible (%s); permitiendo '%s' sin cooldown.", e, action)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
