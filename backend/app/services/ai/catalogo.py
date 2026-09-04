"""Catálogo de modelos de IA: lectura, siembra y retiro (spec 10).

El catálogo pasa de ser código a ser dato. Este módulo es lo único que lo lee, y
todo lo demás —el selector de la interfaz, la resolución del modelo efectivo, la
estimación de costo— pasa por acá.

Dos decisiones que conviene tener a la vista:

**La semilla incluye el precio.** Los modelos y sus tarifas vivían en dos
diccionarios distintos (`MODELS_BY_PROVIDER` y `MODEL_PRICING`) que nadie
obligaba a coincidir. Un modelo elegible sin tarifa se registraba con
`estimated_cost_usd = NULL`, y el gasto de esa organización aparecía como cero en
el panel. Al unificarlos en una fila, el desalineamiento deja de ser posible: se
siembran juntos y `verificar_catalogo_ia.py` comprueba que ninguno quede sin
precio.

**Un modelo retirado no rompe a quien lo usa.** Se deja de ofrecer y la
organización queda SEÑALADA. La alternativa —resolver a otro modelo -- es
exactamente el cambio silencioso que la spec prohíbe: el cliente descubriría el
cambio de modelo y de costo en la factura.
"""
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model_catalog import (
    ESTADO_DISPONIBLE,
    ESTADO_EN_RETIRADA,
    ESTADO_RETIRADO,
    AIModelCatalog,
)
from app.models.organization import Organization

logger = logging.getLogger("lejovista.ai.catalogo")

PROVEEDORES = ("openai", "gemini_direct", "openrouter")


class ModeloNoElegible(ValueError):
    """Se intentó fijar un modelo que el catálogo no ofrece.

    Lleva el motivo encima: "no existe" y "está retirado" se resuelven distinto
    —el primero es un error de tipeo, el segundo exige elegir otro— y un mensaje
    único obligaría a averiguar cuál de los dos fue.
    """

    def __init__(self, provider: str, model: str, motivo: str):
        self.provider = provider
        self.model = model
        self.motivo = motivo
        super().__init__(f"{provider}/{model}: {motivo}")


# Semilla inicial. Es el contenido EXACTO que tenían `MODELS_BY_PROVIDER` y
# `MODEL_PRICING` en el código, para que sembrar el catálogo no cambie el
# comportamiento de ninguna organización el primer día. A partir de acá se
# administra como dato.
#
# Los precios son por 1M de tokens en USD. `cache` es la tarifa del input
# servido desde la caché del proveedor; ausente significa input a tarifa plena,
# que sobrestima — la dirección segura para un número que decide cuánto gastar.
SEMILLA: List[Dict[str, Any]] = [
    # OpenAI. Estos cuatro son los permitidos en el proyecto de la agencia;
    # `gpt-5.4` estándar se excluye a propósito (ver el comentario histórico en
    # `llm.py`): dejarlo elegible hacía que el cliente lo guardara y todas sus
    # llamadas fallaran sin que la interfaz dijera por qué.
    dict(provider="openai", model="gpt-5.6-luna", label="GPT-5.6 Luna",
         input="0.20", cache="0.02", output="1.20", vision=True, orden=10),
    dict(provider="openai", model="gpt-5.6-sol", label="GPT-5.6 Sol",
         input="5.00", cache="0.50", output="30.00", vision=True, orden=20),
    dict(provider="openai", model="gpt-5.4-mini", label="GPT-5.4 Mini (rápido y económico)",
         input="0.25", output="2.00", vision=True, orden=30),
    dict(provider="openai", model="gpt-5.4-nano", label="GPT-5.4 Nano (el más rápido)",
         input="0.05", output="0.40", vision=False, orden=40),

    dict(provider="gemini_direct", model="gemini-3.5-flash",
         label="Gemini 3.5 Flash (más rápido y avanzado)",
         input="0.15", output="0.60", vision=True, orden=10),
    dict(provider="gemini_direct", model="gemini-2.5-flash", label="Gemini 2.5 Flash (estándar)",
         input="0.15", output="0.60", vision=True, orden=20),
    dict(provider="gemini_direct", model="gemini-2.5-pro", label="Gemini 2.5 Pro (avanzado)",
         input="1.25", output="10.00", vision=True, orden=30),

    dict(provider="openrouter", model="google/gemini-3.5-flash",
         label="Gemini 3.5 Flash (más rápido y avanzado)",
         input="0.15", output="0.60", vision=True, orden=10),
    dict(provider="openrouter", model="google/gemini-2.5-flash", label="Gemini 2.5 Flash (estándar)",
         input="0.15", output="0.60", vision=True, orden=20),
    dict(provider="openrouter", model="google/gemini-2.5-pro", label="Gemini 2.5 Pro (avanzado)",
         input="1.25", output="10.00", vision=True, orden=30),
    dict(provider="openrouter", model="anthropic/claude-3-5-sonnet",
         label="Claude 3.5 Sonnet (premium)",
         input="3.00", output="15.00", vision=True, orden=40),
]


def _fila(entrada: Dict[str, Any]) -> AIModelCatalog:
    return AIModelCatalog(
        provider=entrada["provider"],
        model=entrada["model"],
        label=entrada["label"],
        soporta_vision=bool(entrada.get("vision", False)),
        soporta_json=bool(entrada.get("json", True)),
        soporta_tools=bool(entrada.get("tools", True)),
        precio_input=Decimal(entrada["input"]) if entrada.get("input") else None,
        precio_output=Decimal(entrada["output"]) if entrada.get("output") else None,
        precio_cache=Decimal(entrada["cache"]) if entrada.get("cache") else None,
        estado=entrada.get("estado", ESTADO_DISPONIBLE),
        orden=entrada.get("orden", 0),
    )


async def sembrar(db: AsyncSession) -> int:
    """Inserta los modelos de la semilla que todavía no estén en el catálogo.

    Idempotente y NO destructiva: una fila existente no se toca, porque puede
    haber sido editada a mano —un precio corregido, un modelo puesto en
    retirada— y sembrar no debe revertir esa decisión.
    """
    presentes = {
        (p, m) for p, m in (await db.execute(
            select(AIModelCatalog.provider, AIModelCatalog.model)
        )).all()
    }
    nuevos = 0
    for entrada in SEMILLA:
        if (entrada["provider"], entrada["model"]) in presentes:
            continue
        db.add(_fila(entrada))
        nuevos += 1
    if nuevos:
        await db.flush()
    return nuevos


async def modelos_de(
    db: AsyncSession, provider: str, *, solo_elegibles: bool = True
) -> List[AIModelCatalog]:
    """Modelos del proveedor. Por defecto solo los que se pueden ELEGIR.

    `solo_elegibles=False` incluye los que están en retirada y los retirados: lo
    necesita la vista de administración del catálogo, no el selector del cliente.
    """
    q = select(AIModelCatalog).where(AIModelCatalog.provider == provider)
    if solo_elegibles:
        q = q.where(AIModelCatalog.estado == ESTADO_DISPONIBLE)
    q = q.order_by(AIModelCatalog.orden, AIModelCatalog.model)
    return list((await db.execute(q)).scalars().all())


async def obtener(db: AsyncSession, provider: str, model: str) -> Optional[AIModelCatalog]:
    return (await db.execute(
        select(AIModelCatalog).where(
            AIModelCatalog.provider == provider,
            AIModelCatalog.model == model,
        )
    )).scalar_one_or_none()


async def assert_elegible(db: AsyncSession, provider: str, model: str) -> AIModelCatalog:
    """El modelo se puede fijar en una Organización, o se explica por qué no."""
    fila = await obtener(db, provider, model)
    if fila is None:
        raise ModeloNoElegible(provider, model, "no está en el catálogo de este proveedor")
    if fila.estado == ESTADO_RETIRADO:
        motivo = fila.motivo_retiro or "está retirado"
        raise ModeloNoElegible(provider, model, motivo)
    if fila.estado == ESTADO_EN_RETIRADA:
        raise ModeloNoElegible(
            provider, model,
            "está en retirada y ya no se puede elegir; las organizaciones que lo "
            "usan siguen funcionando",
        )
    return fila


async def precio_de(
    db: AsyncSession, provider: str, model: str
) -> Optional[Dict[str, Decimal]]:
    """Tarifas del modelo, o `None` si el catálogo no las tiene.

    `None` NO es cero. Un modelo sin tarifa deja el costo sin estimar, y quien
    consume ese valor tiene que decidir qué hacer con la ausencia: el
    presupuesto de IA la trata como desconocida, no como gratis.
    """
    fila = await obtener(db, provider, model)
    if fila is None or fila.precio_input is None or fila.precio_output is None:
        return None
    return {
        "input": Decimal(fila.precio_input),
        "output": Decimal(fila.precio_output),
        "cache": Decimal(fila.precio_cache) if fila.precio_cache is not None else Decimal(fila.precio_input),
    }


async def retirar(
    db: AsyncSession, provider: str, model: str, *, motivo: str,
    definitivo: bool = False,
) -> List[Organization]:
    """Marca un modelo como en retirada (o retirado) y DEVUELVE a quién afecta.

    Devolver las organizaciones señaladas es el punto entero de la operación. Un
    retiro que solo cambia una fila y no dice a quién dejó apuntando a un modelo
    que ya no se ofrece es un retiro silencioso: la primera noticia sería una
    llamada fallando en producción.

    Ninguna organización se reasigna automáticamente. Cambiarle el modelo a un
    cliente sin que nadie lo decida es lo que el criterio 9 prohíbe, y el costo
    del modelo nuevo puede ser un múltiplo del anterior.
    """
    fila = await obtener(db, provider, model)
    if fila is None:
        raise ModeloNoElegible(provider, model, "no está en el catálogo de este proveedor")

    fila.estado = ESTADO_RETIRADO if definitivo else ESTADO_EN_RETIRADA
    fila.motivo_retiro = motivo

    afectadas = list((await db.execute(
        select(Organization).where(
            Organization.llm_provider == provider,
            Organization.ai_model == model,
        )
    )).scalars().all())

    await db.flush()

    if afectadas:
        logger.warning(
            "Modelo %s/%s marcado '%s' (%s). Organizaciones que lo usan: %s. "
            "NO se reasignaron: hay que elegirles otro modelo a mano.",
            provider, model, fila.estado, motivo,
            ", ".join(o.slug for o in afectadas),
        )
    return afectadas


async def organizaciones_con_modelo_no_elegible(db: AsyncSession) -> List[Dict[str, Any]]:
    """Organizaciones apuntando a un modelo que el catálogo ya no ofrece.

    Es la consulta que alimenta el aviso de la agencia. Incluye tanto el modelo
    ausente del catálogo como el retirado o en retirada: los tres significan que
    esa organización necesita una decisión.
    """
    catalogo = {
        (f.provider, f.model): f
        for f in (await db.execute(select(AIModelCatalog))).scalars().all()
    }
    filas: List[Dict[str, Any]] = []
    orgs = (await db.execute(
        select(Organization).where(Organization.llm_provider.is_not(None))
    )).scalars().all()
    for org in orgs:
        fila = catalogo.get((org.llm_provider, org.ai_model))
        if fila is not None and fila.estado == ESTADO_DISPONIBLE:
            continue
        filas.append({
            "organization_id": org.id,
            "slug": org.slug,
            "provider": org.llm_provider,
            "model": org.ai_model,
            "estado": fila.estado if fila else "ausente",
            "motivo": (fila.motivo_retiro if fila else None) or (
                None if fila else "el modelo no está en el catálogo de ese proveedor"
            ),
        })
    return filas
