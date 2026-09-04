"""Registro de descartes de salidas del modelo (spec 10).

Canal lateral, igual que `token_tracking`: **nunca** debe romper la respuesta que
está evaluando. Usa su propia sesión y se traga cualquier fallo. Un registro que
puede tumbar la conversación es peor que no tener registro.

El modo de registro (`GROUNDING_MODO_REGISTRO=true`) es lo que la spec pide para
la activación: se anota qué se DESCARTARÍA sobre tráfico real, sin descartar
nada, y se mide durante unos días antes de encender el descarte. Es la respuesta
al riesgo declarado en la spec —"un grounding demasiado estricto descarta
respuestas legítimas y el analista deja de analizar"— que ya ocurrió una vez, con
un tope mal calibrado y el síntoma de respuestas de plantilla.
"""
import logging
from typing import Optional

from app.config import settings
from app.database import async_session
from app.models.ai_discard import MAX_EXTRACTO, AIDiscard

logger = logging.getLogger("lejovista.ai.descartes")


def descarte_activo() -> bool:
    """¿Se descarta de verdad, o solo se anota lo que se descartaría?"""
    return not getattr(settings, "GROUNDING_MODO_REGISTRO", False)


async def registrar(
    *,
    motivo: str,
    feature: str,
    organization_id: Optional[str] = None,
    ad_account_id: Optional[str] = None,
    detalle: Optional[str] = None,
    extracto: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
    aplicado: Optional[bool] = None,
) -> None:
    """Anota un descarte. Silencioso ante cualquier fallo.

    `aplicado=None` toma el valor del modo global: es lo que hace que el mismo
    llamador sirva para el modo de registro y para el descarte real sin
    duplicar la lógica en cada punto de guarda.
    """
    if aplicado is None:
        aplicado = descarte_activo()

    nivel = logger.warning if aplicado else logger.info
    nivel(
        "Descarte de IA (%s) motivo=%s detalle=%s aplicado=%s",
        feature, motivo, detalle, aplicado,
    )

    try:
        fila = AIDiscard(
            organization_id=organization_id,
            ad_account_id=ad_account_id,
            feature=feature,
            motivo=motivo,
            detalle=(detalle or "")[:255] or None,
            extracto=(extracto or "")[:MAX_EXTRACTO] or None,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            aplicado=aplicado,
        )
        async with async_session() as session:
            session.add(fila)
            await session.commit()
    except Exception:
        logger.exception("No se pudo registrar el descarte (motivo=%s).", motivo)
