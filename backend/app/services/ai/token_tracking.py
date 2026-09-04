"""Medición de consumo de tokens del LLM (Ajuste 3).

Captura el bloque `usage` que devuelven OpenAI / Gemini (endpoint compat) /
OpenRouter en cada respuesta y lo persiste en `AITokenUsage`, etiquetado por la
funcionalidad que originó la llamada. Es un canal lateral: NUNCA debe romper la
llamada real al LLM, por eso usa su propia sesión de BD y traga cualquier
excepción (solo la registra en el log).
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.database import async_session
from app.models.ai_model_catalog import AIModelCatalog
from app.models.ai_token_usage import AITokenUsage

logger = logging.getLogger("lejovista.tokens")


# Precios por 1M de tokens en USD (input, output). RESPALDO en código del
# catálogo de modelos (`ai/catalogo.py`), que es la fuente autoritativa desde la
# spec 10. Se conserva para el arranque anterior a la siembra y para los
# entornos que corren sin catálogo poblado; si un modelo no está en ninguno de
# los dos, `estimated_cost_usd` queda en None (no se inventa un 0).
#
# Antes ESTE diccionario era la única fuente, y vivía separado del de modelos
# elegibles: un modelo se podía elegir sin tener tarifa acá, y entonces su
# consumo se contabilizaba como cero. En el catálogo las dos cosas son la misma
# fila y el desalineamiento deja de ser representable.
#
# Las claves se comparan por prefijo, tolerando sufijos de fecha/región y el
# prefijo "google/" que usa OpenRouter.
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # `cached` es la tarifa del input que OpenAI sirve desde su cache: ~10x mas
    # barato. Es opcional -- un modelo sin `cached` cobra TODO el input a tarifa
    # plena, que sobrestima el costo. Se prefiere sobrestimar antes que
    # subestimar: este numero se usa para decidir cuanto gastar.
    "gpt-5.6-luna": {"input": 0.20, "cached": 0.02, "output": 1.20},
    "gpt-5.6-sol": {"input": 5.00, "cached": 0.50, "output": 30.00},
    "gpt-5.4-nano": {"input": 0.05, "output": 0.40},
    "gpt-5.4-mini": {"input": 0.25, "output": 2.00},
    "gpt-5.4": {"input": 1.25, "output": 10.00},
    "gemini-3.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
}


@dataclass
class UsageContext:
    """Contexto ligero que viaja hasta el cliente LLM para poder atribuir el
    consumo a una organización/cuenta y a una funcionalidad concreta."""
    feature: str
    organization_id: Optional[str] = None
    ad_account_id: Optional[str] = None


def _normalize_model(model: str) -> str:
    """Quita el prefijo de proveedor de OpenRouter ('google/gemini-...')."""
    return model.split("/", 1)[1] if "/" in model else model


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    precio: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    """Costo estimado en USD. Devuelve None si el modelo no está en la tabla.

    Tres detalles que hacen que el número sea el real y no uno aproximado:

    * Los tokens de razonamiento facturan como output y ya vienen incluidos en
      `completion_tokens` en las APIs compatibles, así que no se suman aparte.
    * `cached_tokens` viene DENTRO de `prompt_tokens` (no se suma al total): se
      resta para cobrar solo la parte no cacheada a tarifa plena.
    * Si el modelo no declara tarifa de cacheado, todo el input va a tarifa
      plena — sobrestima, que es la dirección segura.
    """
    key = _normalize_model(model)
    price = precio or MODEL_PRICING.get(key)
    if not price:
        # Búsqueda tolerante por prefijo (p. ej. "gpt-5.4-mini-2026-01").
        price = next((v for k, v in MODEL_PRICING.items() if key.startswith(k)), None)
    if not price:
        return None

    # Clamp defensivo: si el proveedor reportara mas cacheados que prompt
    # tokens, restarlos daria un input negativo y un costo por debajo del real.
    cached = max(0, min(int(cached_tokens or 0), int(prompt_tokens or 0)))
    sin_cachear = max(0, int(prompt_tokens or 0) - cached)
    tarifa_cache = price.get("cached", price["input"])

    cost = (
        (sin_cachear / 1_000_000) * price["input"]
        + (cached / 1_000_000) * tarifa_cache
        + (completion_tokens / 1_000_000) * price["output"]
    )
    return round(cost, 6)


async def _tarifa_del_catalogo(session, provider: str, model: str) -> Optional[Dict[str, float]]:
    """Tarifas desde el catálogo (fuente autoritativa desde la spec 10).

    Se consulta en la MISMA sesión que ya se abre para grabar la fila: es una
    consulta por índice único, despreciable al lado de la llamada al modelo que
    se está midiendo, y evita una caché que se quedaría vieja justo después de
    corregir un precio.
    """
    fila = (await session.execute(
        select(AIModelCatalog).where(
            AIModelCatalog.provider == provider,
            AIModelCatalog.model == model,
        )
    )).scalar_one_or_none()
    if fila is None or fila.precio_input is None or fila.precio_output is None:
        return None
    precio = {"input": float(fila.precio_input), "output": float(fila.precio_output)}
    if fila.precio_cache is not None:
        precio["cached"] = float(fila.precio_cache)
    return precio


async def record_token_usage(
    ctx: Optional[UsageContext],
    provider: str,
    model: str,
    usage: Optional[Dict[str, Any]],
    prompt_version: Optional[str] = None,
) -> None:
    """Persiste una fila de consumo. Silencioso ante cualquier fallo — medir el
    gasto jamás debe tumbar la funcionalidad que se está midiendo.

    `prompt_version` (spec 10, criterio 7) es lo que permite acotar en el tiempo
    una regresión de calidad después de tocar un prompt. Sin ella se ve el
    modelo y el costo, pero no QUÉ instrucciones recibió el modelo, y la
    investigación se convierte en arqueología de commits.
    """
    if ctx is None or not usage:
        return
    try:
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", 0) or (prompt_tokens + completion_tokens))

        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = int(details.get("reasoning_tokens", 0) or 0)

        # Input servido desde la cache del proveedor. Viene DENTRO de
        # `prompt_tokens`, no aparte. Gemini lo llama `cached_content_token_count`.
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = int(
            prompt_details.get("cached_tokens")
            or usage.get("cached_content_token_count")
            or 0
        )

        async with async_session() as session:
            tarifa = await _tarifa_del_catalogo(session, provider, _normalize_model(model))
            costo = estimate_cost_usd(
                model, prompt_tokens, completion_tokens, cached_tokens, precio=tarifa,
            )
            session.add(AITokenUsage(
                organization_id=ctx.organization_id,
                ad_account_id=ctx.ad_account_id,
                feature=ctx.feature,
                provider=provider,
                model=_normalize_model(model),
                prompt_version=prompt_version,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_tokens=cached_tokens,
                total_tokens=total_tokens,
                used_cot=reasoning_tokens > 0,
                estimated_cost_usd=costo,
                # La columna que decide contra el presupuesto mensual de IA. Se
                # construye desde `repr` del float para no arrastrar el residuo
                # binario a una comparación que autoriza gasto (spec 02).
                estimated_cost_usd_exact=None if costo is None else Decimal(repr(costo)),
            ))
            await session.commit()
    except Exception:
        logger.exception("No se pudo registrar el consumo de tokens (feature=%s).", getattr(ctx, "feature", "?"))
