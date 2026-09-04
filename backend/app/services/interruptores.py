"""Interruptores operativos: apagar una capacidad sin desplegar (spec 13).

**Un interruptor que solo oculta un botón no contiene nada.** La capacidad se
rechaza donde se EJECUTA: en la API y también en las tareas asíncronas ya
encoladas. Durante un incidente, lo que hay que cortar es justamente la tarea
que ya salió; cortar solo la puerta de entrada deja la cola vaciándose sobre el
problema.

**El estado es dato, no configuración.** Cambiarlo tiene efecto inmediato y
queda registrado con actor y momento. Un interruptor que exige un despliegue no
sirve en el único momento en que se usa.

**Fallo cerrado para lo que actúa, abierto para lo que consulta.** Si el almacén
de estado no responde, las cinco capacidades se consideran apagadas y la lectura
del histórico sigue. Es la política de `01` aplicada al plano operativo, y acota
otra vez la ADR-13: el estado de los interruptores no puede degradar abierto,
porque el caso en que falla es precisamente un incidente.

La lectura del histórico NUNCA pasa por aquí. No es que su interruptor esté
encendido: es que no tiene. Un cliente que consulta lo que ya ocurrió no puede
romper nada, y quitarle esa consulta durante un incidente lo deja sin saber qué
está pasando con su dinero.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operacion import (
    CAP_SOLO_LECTURA,
    CAPACIDADES,
    DESCRIPCION_DE_LA_CAPACIDAD,
    OperationalChange,
    OperationalSwitch,
)

logger = logging.getLogger("lejovista.interruptores")

# Cuánto se cachea el estado en memoria del proceso. Corto a propósito: un
# interruptor tiene que hacer efecto "inmediato", y cinco segundos es lo que se
# tarda en recargar una pantalla. Sin caché, cada petición haría una consulta a
# una tabla que casi siempre está vacía.
TTL_CACHE_SEGUNDOS = 5

_cache: Dict[str, Tuple[float, Dict[Tuple[str, Optional[str]], bool]]] = {}


class CapacidadApagada(Exception):
    """La capacidad está apagada por un interruptor operativo.

    Nombra el interruptor responsable (criterio 3). Un rechazo que no dice cuál
    obliga a revisar los cinco durante un incidente, y quien lo revisa está
    ocupado.
    """

    code = "CAPABILITY_DISABLED"

    def __init__(self, capacidad: str, *, motivo: Optional[str] = None,
                 ambito: str = "global"):
        self.capacidad = capacidad
        self.motivo = motivo
        self.ambito = ambito
        super().__init__(
            f"La capacidad '{capacidad}' está apagada ({ambito})."
            + (f" Motivo: {motivo}" if motivo else "")
        )

    def as_detail(self) -> dict:
        """Lo que ve el CLIENTE. Sin detalles de infraestructura.

        El diagnóstico técnico —qué interruptor, quién lo movió, por qué— vive
        en la vista de la agencia (spec 13, criterio 13). Lo que el cliente
        necesita saber es qué puede y qué no puede hacer ahora.
        """
        return {
            "code": self.code,
            # El nombre interno viaja para que la interfaz pueda decidir qué
            # ocultar, pero el mensaje no lo menciona.
            "capacidad": self.capacidad,
            "message": _MENSAJE_PARA_EL_CLIENTE.get(
                self.capacidad,
                "Esta función no está disponible en este momento. Tus datos y tu "
                "historial siguen accesibles.",
            ),
        }

    def as_diagnostico(self) -> dict:
        """Lo que ve la AGENCIA: qué interruptor, en qué ámbito y por qué."""
        return {
            "code": self.code,
            "capacidad": self.capacidad,
            "descripcion": DESCRIPCION_DE_LA_CAPACIDAD.get(self.capacidad, self.capacidad),
            "ambito": self.ambito,
            "motivo": self.motivo,
        }


# Cada mensaje nombra lo que SIGUE funcionando. Es lo que decide si el cliente
# puede seguir trabajando o tiene que volver luego, y es la diferencia entre un
# aviso y un susto.
_MENSAJE_PARA_EL_CLIENTE = {
    "creacion": (
        "Ahora mismo no se pueden crear campañas nuevas. Las que ya tenés "
        "siguen funcionando con normalidad y podés consultar todos tus datos."
    ),
    "write_back": (
        "Ahora mismo no se pueden aplicar cambios sobre tus campañas en Meta. "
        "Todo lo demás sigue igual, y tus campañas siguen corriendo como están."
    ),
    "sync": (
        "No estamos trayendo datos nuevos de Meta en este momento. Lo que ves es "
        "de la última actualización; en cuanto se restablezca, se pone al día solo."
    ),
    "ia": (
        "El asistente que redacta no está disponible ahora. Tus datos, tus "
        "métricas y tus informes siguen funcionando igual."
    ),
    "medios": (
        "No se pueden subir fotos ni videos en este momento. El resto del "
        "producto funciona con normalidad."
    ),
    CAP_SOLO_LECTURA: (
        "Estamos haciendo una tarea de mantenimiento, así que por un rato no se "
        "pueden hacer cambios. Podés seguir consultando todos tus datos y tu "
        "historial con normalidad."
    ),
}


@dataclass(frozen=True)
class EstadoDeCapacidad:
    capacidad: str
    encendida: bool
    ambito: str = "global"          # global | organizacion | por_defecto | fallo
    motivo: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capacidad": self.capacidad,
            "descripcion": DESCRIPCION_DE_LA_CAPACIDAD.get(self.capacidad, self.capacidad),
            "encendida": self.encendida,
            "ambito": self.ambito,
            "motivo": self.motivo,
        }


def invalidar_cache() -> None:
    """Tras cambiar un interruptor. Es lo que hace que el efecto sea inmediato."""
    _cache.clear()


async def _cargar(db: AsyncSession) -> Dict[Tuple[str, Optional[str]], Any]:
    """Todas las filas, indexadas por (capacidad, organización).

    Se cargan TODAS de una vez y no la que se pregunta: la tabla tiene como
    mucho unas decenas de filas —solo existe lo apagado— y una consulta por
    capacidad multiplicaría por cinco el coste de cada petición.
    """
    ahora = time.monotonic()
    guardado = _cache.get("todo")
    if guardado and ahora - guardado[0] < TTL_CACHE_SEGUNDOS:
        return guardado[1]

    filas = (await db.execute(select(OperationalSwitch))).scalars().all()
    indice = {
        (f.capacidad, f.organization_id): (f.encendido, f.motivo)
        for f in filas
    }
    _cache["todo"] = (ahora, indice)
    return indice


async def estado(
    db: AsyncSession, capacidad: str, *, organization_id: Optional[str] = None,
) -> EstadoDeCapacidad:
    """Si la capacidad está encendida para esta Organización.

    Resolución determinista: la fila de la Organización manda sobre la global, y
    la ausencia de las dos significa encendido. El modo de solo lectura se
    comprueba ANTES: es una capacidad que apaga todas las demás de una vez, y
    resolverlo después dejaría un hueco entre que se enciende y que se propaga.
    """
    try:
        indice = await _cargar(db)
    except Exception:
        # Fallo cerrado (criterio 7). El almacén no responde justo cuando más
        # falta hace saber qué está apagado, así que se asume lo peor para todo
        # lo que ACTÚA. La lectura del histórico no pasa por aquí y sigue.
        logger.exception(
            "No se pudo leer el estado de los interruptores; se apagan las "
            "capacidades de escritura por precaución (capacidad=%s).", capacidad,
        )
        return EstadoDeCapacidad(
            capacidad, encendida=False, ambito="fallo",
            motivo=(
                "No se pudo comprobar el estado de los interruptores operativos. "
                "Por precaución, las operaciones de escritura quedan bloqueadas."
            ),
        )

    if capacidad != CAP_SOLO_LECTURA:
        activo, motivo = _solo_lectura_desde(indice, organization_id)
        if activo:
            return EstadoDeCapacidad(
                capacidad, encendida=False, ambito="solo_lectura",
                motivo=motivo or "El sistema está en modo de solo lectura.",
            )

    resuelto = _resolver(indice, capacidad, organization_id)
    if resuelto is None:
        return EstadoDeCapacidad(capacidad, encendida=True, ambito="por_defecto")

    encendida, motivo = resuelto
    ambito = "organizacion" if (capacidad, organization_id) in indice else "global"
    return EstadoDeCapacidad(capacidad, encendida=encendida, ambito=ambito, motivo=motivo)


def _resolver(indice, capacidad: str, organization_id: Optional[str]):
    if organization_id and (capacidad, organization_id) in indice:
        return indice[(capacidad, organization_id)]
    if (capacidad, None) in indice:
        return indice[(capacidad, None)]
    return None


def _solo_lectura_desde(indice, organization_id: Optional[str]) -> Tuple[bool, Optional[str]]:
    """¿Está activo el modo de solo lectura? Y por qué.

    Se lee aparte de `estado()` porque su `encendido` significa lo CONTRARIO:
    en las cinco capacidades, encendido = la capacidad funciona; en el modo de
    solo lectura, encendido = está bloqueando. Mezclarlos en la misma función
    produciría, tarde o temprano, un `not` en el sitio equivocado — y el sitio
    equivocado aquí es el que decide si se puede escribir durante un incidente.
    """
    fila = _resolver(indice, CAP_SOLO_LECTURA, organization_id)
    if fila is None:
        return False, None
    activo, motivo = fila
    return bool(activo), motivo


async def assert_encendida(
    db: AsyncSession, capacidad: str, *, organization_id: Optional[str] = None,
) -> None:
    """Levanta `CapacidadApagada` si no lo está. El punto de aplicación."""
    resultado = await estado(db, capacidad, organization_id=organization_id)
    if not resultado.encendida:
        raise CapacidadApagada(
            capacidad, motivo=resultado.motivo, ambito=resultado.ambito,
        )


async def esta_encendida(
    db: AsyncSession, capacidad: str, *, organization_id: Optional[str] = None,
) -> bool:
    return (await estado(db, capacidad, organization_id=organization_id)).encendida


async def solo_lectura_activo(
    db: AsyncSession, *, organization_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """¿Está el sistema en modo de solo lectura, y por qué?

    Ante un fallo del almacén se responde que SÍ: no poder comprobarlo es, a
    efectos de contención, indistinguible de estar activo, y equivocarse hacia
    "se puede escribir" es el error caro.
    """
    try:
        indice = await _cargar(db)
    except Exception:
        logger.exception(
            "No se pudo comprobar el modo de solo lectura; se asume activo.",
        )
        return True, (
            "No se pudo comprobar el estado operativo. Por precaución, las "
            "escrituras quedan bloqueadas."
        )
    return _solo_lectura_desde(indice, organization_id)


async def panorama(
    db: AsyncSession, *, organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    """El estado de todas las capacidades. Para la vista de operación."""
    activo, motivo_lectura = await solo_lectura_activo(
        db, organization_id=organization_id,
    )
    capacidades = [
        (await estado(db, c, organization_id=organization_id)).as_dict()
        for c in CAPACIDADES
    ]
    return {
        "solo_lectura": activo,
        "solo_lectura_motivo": motivo_lectura,
        "capacidades": capacidades,
        "hay_algo_apagado": activo or any(not c["encendida"] for c in capacidades),
    }


async def cambiar(
    db: AsyncSession,
    capacidad: str,
    *,
    encendido: bool,
    organization_id: Optional[str] = None,
    motivo: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> OperationalSwitch:
    """Enciende o apaga una capacidad, con constancia (criterio 4).

    La invalidación del caché va al final y no al principio: si algo falla al
    escribir, el caché sigue describiendo el estado real en vez de forzar una
    relectura de un cambio que no se guardó.
    """
    if capacidad not in CAPACIDADES and capacidad != CAP_SOLO_LECTURA:
        raise ValueError(
            f"'{capacidad}' no es una capacidad conocida. Las válidas son: "
            f"{', '.join((CAP_SOLO_LECTURA,) + CAPACIDADES)}."
        )

    fila = (await db.execute(
        select(OperationalSwitch).where(
            OperationalSwitch.capacidad == capacidad,
            OperationalSwitch.organization_id == organization_id,
        )
    )).scalar_one_or_none()

    anterior = None if fila is None else fila.encendido

    if fila is None:
        fila = OperationalSwitch(
            capacidad=capacidad, organization_id=organization_id,
        )
        db.add(fila)

    fila.encendido = encendido
    fila.motivo = (motivo or "").strip() or None
    fila.actualizado_por_user_id = actor_user_id
    fila.actualizado_por_email = actor_email

    db.add(OperationalChange(
        tipo="interruptor",
        clave=capacidad,
        organization_id=organization_id,
        valor_anterior=None if anterior is None else ("encendido" if anterior else "apagado"),
        valor_nuevo="encendido" if encendido else "apagado",
        motivo=fila.motivo,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
    ))

    await db.flush()
    invalidar_cache()

    nivel = logger.warning if not encendido else logger.info
    nivel(
        "Interruptor '%s' %s para %s por %s. Motivo: %s",
        capacidad, "APAGADO" if not encendido else "encendido",
        organization_id or "TODAS las organizaciones",
        actor_email or actor_user_id or "?", fila.motivo or "(sin motivo)",
    )
    return fila


# --- Aplicación en las tareas asíncronas ------------------------------------

def assert_encendida_sync(
    capacidad: str, *, organization_id: Optional[str] = None,
) -> None:
    """La misma comprobación desde una tarea de Celery, que no es asíncrona.

    Existe porque el criterio 2 exige que el interruptor corte también las
    tareas YA ENCOLADAS. Comprobarlo solo en la API deja la cola vaciándose
    sobre el problema que se acaba de intentar contener — y en un incidente esa
    cola suele ser justo lo que hay que parar.

    Abre su propia sesión: una tarea de Celery no tiene la del llamador, y
    heredarla no es posible desde otro proceso.
    """
    from app.database import async_session

    async def _comprobar():
        async with async_session() as db:
            await assert_encendida(db, capacidad, organization_id=organization_id)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_comprobar())
        return
    # Dentro de un loop ya corriendo (algunas rutas de test): se delega al
    # llamador, que puede await-earlo.
    raise RuntimeError(
        "assert_encendida_sync se llamó dentro de un loop asíncrono; usá "
        "assert_encendida directamente."
    )
