"""Feature flags por Organización y por cuenta piloto (spec 13, 8-9).

**Infraestructura compartida, no una solución por spec.** Cuatro specs
anteriores necesitan encender algo gradualmente —las experiencias de cliente y
agencia, la versión candidata de la API de Meta, el chat proactivo, el
endurecimiento del grounding— y cada una tendría que inventar su propio
mecanismo. Cuatro mecanismos distintos significan cuatro sitios donde mirar
cuando algo está encendido para quien no debería.

**El valor predeterminado vive en el código, junto al flag.** Un flag sin fila
tiene un valor CONOCIDO, no un `NULL` que cada consumidor interprete a su
manera. Declararlo aquí obliga además a pensar qué pasa con quien todavía no lo
tiene, que es la pregunta que se olvida.

**La cuenta piloto es un caso de uso, no un concepto aparte.** Es simplemente el
ámbito más estrecho: probar una capacidad con una cuenta y no con una
organización entera es la diferencia entre un error acotado y un cliente
enfadado.

Resolución, de lo más específico a lo más general: cuenta → organización →
global → valor predeterminado. Determinista, y en ese orden: si la cuenta piloto
no ganara a la organización, no serviría para pilotar.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operacion import FeatureFlag, OperationalChange

logger = logging.getLogger("lejovista.flags")

TTL_CACHE_SEGUNDOS = 5
_cache: Dict[str, Tuple[float, Dict[Tuple[str, Optional[str], Optional[str]], bool]]] = {}


@dataclass(frozen=True)
class FlagDeclarado:
    clave: str
    descripcion: str
    por_defecto: bool = False


# El catálogo. Añadir un flag aquí es lo que lo hace existir: uno que solo
# aparece en una fila de la base no se puede listar, ni documentar, ni saber qué
# hace cuando nadie lo ha configurado.
CATALOGO: Tuple[FlagDeclarado, ...] = (
    FlagDeclarado(
        "chat_resumen_proactivo",
        "El chat abre con un resumen del periodo reciente (spec 12).",
        por_defecto=True,
    ),
    FlagDeclarado(
        "chat_avisos_proactivos",
        "El chat entrega avisos de anomalías al abrirse (spec 12).",
        por_defecto=True,
    ),
    FlagDeclarado(
        "chat_brief_de_negocio",
        "El chat recoge el brief mínimo de negocio antes de proponer (spec 12).",
        por_defecto=True,
    ),
    FlagDeclarado(
        "evidencia_en_la_propuesta",
        "La propuesta muestra las campañas comparables que la respaldan (spec 11).",
        por_defecto=True,
    ),
    FlagDeclarado(
        "grounding_descarte_activo",
        "La guarda de grounding descarta de verdad en vez de solo anotar (spec 10).",
        por_defecto=True,
    ),
    FlagDeclarado(
        "meta_version_candidata",
        "Esta cuenta usa la versión candidata de la API de Meta (spec 08).",
        por_defecto=False,
    ),
)

POR_CLAVE = {f.clave: f for f in CATALOGO}


class FlagDesconocido(ValueError):
    """Se preguntó por un flag que no está declarado.

    Se levanta en vez de devolver `False`: un flag mal escrito que responde
    "apagado" produce una capacidad que nunca se enciende y nadie entiende por
    qué. Fallar aquí lo convierte en un error de programación visible.
    """


def invalidar_cache() -> None:
    _cache.clear()


async def _cargar(db: AsyncSession):
    ahora = time.monotonic()
    guardado = _cache.get("todo")
    if guardado and ahora - guardado[0] < TTL_CACHE_SEGUNDOS:
        return guardado[1]

    filas = (await db.execute(select(FeatureFlag))).scalars().all()
    indice = {
        (f.clave, f.organization_id, f.ad_account_id): f.activo
        for f in filas
    }
    _cache["todo"] = (ahora, indice)
    return indice


async def activo(
    db: AsyncSession,
    clave: str,
    *,
    organization_id: Optional[str] = None,
    ad_account_id: Optional[str] = None,
) -> bool:
    """¿Está encendido este flag para este ámbito?

    Ante un fallo del almacén se devuelve el valor PREDETERMINADO, no `False`.
    Es la diferencia con los interruptores, y es deliberada: un interruptor
    apagado contiene un incidente, mientras que un flag apagado por accidente
    apaga una capacidad que llevaba meses funcionando bien. El default es lo
    que el sistema hacía ayer.
    """
    declarado = POR_CLAVE.get(clave)
    if declarado is None:
        raise FlagDesconocido(
            f"'{clave}' no es un feature flag declarado. Los válidos son: "
            f"{', '.join(sorted(POR_CLAVE))}."
        )

    try:
        indice = await _cargar(db)
    except Exception:
        logger.exception(
            "No se pudo leer el estado de los feature flags; se usa el valor "
            "predeterminado de '%s' (%s).", clave, declarado.por_defecto,
        )
        return declarado.por_defecto

    if ad_account_id and (clave, None, ad_account_id) in indice:
        return indice[(clave, None, ad_account_id)]
    if organization_id and (clave, organization_id, None) in indice:
        return indice[(clave, organization_id, None)]
    if (clave, None, None) in indice:
        return indice[(clave, None, None)]
    return declarado.por_defecto


async def resolver_todos(
    db: AsyncSession,
    *,
    organization_id: Optional[str] = None,
    ad_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Todos los flags con su valor efectivo y de dónde sale.

    El origen viaja con el valor porque "está encendido" no dice si lo está por
    su default, por una decisión global o porque esta cuenta es la piloto — y
    apagarlo exige saber cuál de las tres.
    """
    try:
        indice = await _cargar(db)
    except Exception:
        indice = {}

    salida = {}
    for flag in CATALOGO:
        if ad_account_id and (flag.clave, None, ad_account_id) in indice:
            valor, origen = indice[(flag.clave, None, ad_account_id)], "cuenta"
        elif organization_id and (flag.clave, organization_id, None) in indice:
            valor, origen = indice[(flag.clave, organization_id, None)], "organizacion"
        elif (flag.clave, None, None) in indice:
            valor, origen = indice[(flag.clave, None, None)], "global"
        else:
            valor, origen = flag.por_defecto, "por_defecto"
        salida[flag.clave] = {
            "activo": valor,
            "origen": origen,
            "por_defecto": flag.por_defecto,
            "descripcion": flag.descripcion,
        }
    return salida


async def fijar(
    db: AsyncSession,
    clave: str,
    *,
    activo_: bool,
    organization_id: Optional[str] = None,
    ad_account_id: Optional[str] = None,
    nota: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> FeatureFlag:
    """Enciende o apaga un flag en un ámbito, con constancia."""
    if clave not in POR_CLAVE:
        raise FlagDesconocido(f"'{clave}' no es un feature flag declarado.")
    if organization_id and ad_account_id:
        # Los dos a la vez sería un ámbito que la resolución no consulta nunca:
        # la fila existiría y no haría nada, que es peor que un error.
        raise ValueError(
            "Un flag se fija por organización O por cuenta, no por las dos: la "
            "cuenta ya es el ámbito más estrecho."
        )

    fila = (await db.execute(
        select(FeatureFlag).where(
            FeatureFlag.clave == clave,
            FeatureFlag.organization_id == organization_id,
            FeatureFlag.ad_account_id == ad_account_id,
        )
    )).scalar_one_or_none()

    anterior = None if fila is None else fila.activo

    if fila is None:
        fila = FeatureFlag(
            clave=clave, organization_id=organization_id, ad_account_id=ad_account_id,
        )
        db.add(fila)

    fila.activo = activo_
    fila.nota = (nota or "").strip() or None
    fila.actualizado_por_user_id = actor_user_id

    db.add(OperationalChange(
        tipo="flag", clave=clave,
        organization_id=organization_id, ad_account_id=ad_account_id,
        valor_anterior=None if anterior is None else str(anterior),
        valor_nuevo=str(activo_),
        motivo=fila.nota,
        actor_user_id=actor_user_id, actor_email=actor_email,
    ))

    await db.flush()
    invalidar_cache()
    logger.info(
        "Flag '%s' = %s para %s (por %s).",
        clave, activo_,
        ad_account_id or organization_id or "TODAS las organizaciones",
        actor_email or actor_user_id or "?",
    )
    return fila
