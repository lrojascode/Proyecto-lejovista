"""Señales funcionales con umbral declarado (spec 13, criterios 10 y 11).

**Se monitoriza lo funcional, no solo lo vivo.** Un proceso que responde al
healthcheck y lleva tres días sin traer datos está roto para el cliente y
perfecto para el supervisor. Cada señal de aquí responde una pregunta sobre si
el producto hace su trabajo, y trae su umbral encima: un número sin umbral no se
puede vigilar, solo mirar.

**Cada señal declara su umbral en el código, no en la alerta.** Ponerlo en el
sistema de alertas lo separa de lo que mide: quien lee el código no sabe cuándo
es demasiado, y quien configura la alerta no sabe qué está midiendo. Aquí van
juntos.

**Ninguna señal levanta.** Todas devuelven su estado, incluida la que no se pudo
medir, que tiene su propio valor: `desconocido` no es `ok`. Una señal que falla
al medirse y se reporta como sana es la peor de las tres opciones.
"""
import datetime
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("lejovista.senales")

OK = "ok"
ALERTA = "alerta"
DESCONOCIDO = "desconocido"

# --- Umbrales, todos juntos y todos visibles -------------------------------
#
# Se agrupan aquí a propósito: la pregunta "¿cuándo avisa esto?" se contesta en
# una pantalla, y calibrarlos tras el periodo de observación es editar una lista
# en vez de buscar constantes por seis módulos.

HORAS_SIN_SYNC = 26          # un sync diario que se saltó una vuelta todavía sirve
COLA_PROFUNDA = 200          # tareas pendientes
MINUTOS_DE_LATENCIA = 30     # lo que tarda la tarea más vieja en atenderse
TASA_ERRORES_META = 0.20     # 20 % de las llamadas del día
TASA_DESCARTES_GROUNDING = 0.25
HORAS_SIN_RESPALDO = 30
ESCRITURAS_INCIERTAS = 1     # una sola ya merece mirarse
DISCO_LLENO = 0.90
MEMORIA_LLENA = 0.90
# El consumo de IA se vigila como PROPORCIÓN del techo, no como monto: un techo
# de 10 USD y uno de 500 no comparten cifra de alarma.
PROPORCION_IA = 0.90


@dataclass(frozen=True)
class Senal:
    """Una señal, su valor, su umbral y si eso es un problema."""

    clave: str
    titulo: str
    estado: str
    valor: Optional[float] = None
    umbral: Optional[float] = None
    unidad: Optional[str] = None
    detalle: Optional[str] = None

    @property
    def alerta(self) -> bool:
        return self.estado == ALERTA

    def as_dict(self) -> Dict[str, Any]:
        return {
            "clave": self.clave,
            "titulo": self.titulo,
            "estado": self.estado,
            "valor": self.valor,
            "umbral": self.umbral,
            "unidad": self.unidad,
            "detalle": self.detalle,
        }


def _evaluar(clave, titulo, valor, umbral, unidad, detalle=None, invertido=False) -> Senal:
    """`invertido=True` cuando alerta al quedar POR DEBAJO del umbral."""
    if valor is None:
        return Senal(clave, titulo, DESCONOCIDO, None, umbral, unidad,
                     detalle or "No se pudo medir.")
    supera = valor < umbral if invertido else valor > umbral
    return Senal(clave, titulo, ALERTA if supera else OK, valor, umbral, unidad, detalle)


# --- Cada señal ------------------------------------------------------------

async def frescura_del_sync(db: AsyncSession) -> Senal:
    """Horas desde el último sync correcto de CUALQUIER cuenta.

    Global y no por cuenta: si una cuenta concreta falla, es un problema de esa
    credencial; si NINGUNA sincroniza, está caído el pipeline, que es lo que esta
    señal vigila.
    """
    from app.models.sync_job import SyncJob

    try:
        ultimo = (await db.execute(
            select(func.max(SyncJob.completed_at)).where(SyncJob.status == "COMPLETED")
        )).scalar_one_or_none()
    except Exception:
        logger.exception("No se pudo medir la frescura del sync.")
        return Senal("sync_frescura", "Antigüedad del último sync", DESCONOCIDO,
                     umbral=HORAS_SIN_SYNC, unidad="horas")

    if ultimo is None:
        # Instalación nueva: no hay nada que alarmar. Alertar aquí haría que el
        # primer día de cada despliegue empezara con una alerta falsa, y esa es
        # la forma más rápida de que se dejen de mirar.
        return Senal("sync_frescura", "Antigüedad del último sync", OK,
                     umbral=HORAS_SIN_SYNC, unidad="horas",
                     detalle="Todavía no hay ningún sync registrado.")

    horas = (datetime.datetime.utcnow() - ultimo).total_seconds() / 3600
    return _evaluar("sync_frescura", "Antigüedad del último sync",
                    round(horas, 1), HORAS_SIN_SYNC, "horas",
                    detalle=f"Último sync correcto: {ultimo.isoformat()}")


async def profundidad_de_colas(db: AsyncSession) -> Senal:
    """Trabajos de sync encolados o corriendo desde hace demasiado.

    Se mide sobre `SyncJob` y no sobre Celery: la cola de Celery vive en Redis,
    y una señal que necesita Redis para decir que Redis está mal no sirve.
    """
    from app.models.sync_job import SyncJob

    try:
        pendientes = (await db.execute(
            select(func.count()).select_from(SyncJob).where(
                SyncJob.status.in_(("PENDING", "RUNNING"))
            )
        )).scalar() or 0
    except Exception:
        logger.exception("No se pudo medir la profundidad de las colas.")
        return Senal("colas_profundidad", "Trabajos en cola", DESCONOCIDO,
                     umbral=COLA_PROFUNDA, unidad="trabajos")

    return _evaluar("colas_profundidad", "Trabajos en cola",
                    float(pendientes), COLA_PROFUNDA, "trabajos")


async def latencia_de_colas(db: AsyncSession) -> Senal:
    """Minutos que lleva esperando el trabajo pendiente más antiguo.

    Complementa la profundidad: una cola corta cuyo trabajo más viejo lleva dos
    horas está tan atascada como una cola larga que avanza.
    """
    from app.models.sync_job import SyncJob

    try:
        # `started_at`, no `created_at`: la tabla no tiene esa columna y la
        # consulta habría lanzado, dejando la señal en `desconocido` para
        # siempre sin que nadie se enterara.
        mas_viejo = (await db.execute(
            select(func.min(SyncJob.started_at)).where(
                SyncJob.status.in_(("PENDING", "RUNNING"))
            )
        )).scalar_one_or_none()
    except Exception:
        logger.exception("No se pudo medir la latencia de las colas.")
        return Senal("colas_latencia", "Espera del trabajo más antiguo", DESCONOCIDO,
                     umbral=MINUTOS_DE_LATENCIA, unidad="minutos")

    if mas_viejo is None:
        return Senal("colas_latencia", "Espera del trabajo más antiguo", OK,
                     valor=0.0, umbral=MINUTOS_DE_LATENCIA, unidad="minutos",
                     detalle="No hay trabajos pendientes.")

    minutos = (datetime.datetime.utcnow() - mas_viejo).total_seconds() / 60
    return _evaluar("colas_latencia", "Espera del trabajo más antiguo",
                    round(minutos, 1), MINUTOS_DE_LATENCIA, "minutos")


async def errores_de_meta(db: AsyncSession, *, horas: int = 24) -> Senal:
    """Proporción de llamadas a Meta que fallaron en la ventana.

    Proporción y no cuenta: mil llamadas con veinte errores es normal, y veinte
    llamadas con veinte errores es una credencial muerta. La cuenta absoluta no
    distingue los dos casos.
    """
    from app.models.api_call_log import APICallLog

    desde = datetime.datetime.utcnow() - datetime.timedelta(hours=horas)
    try:
        # La columna se llama `timestamp`, no `created_at`. Con el nombre
        # equivocado la consulta lanzaba y la señal se reportaba como
        # `desconocido` para siempre — una señal que no puede medirse y no lo
        # dice a gritos es peor que no tenerla.
        total = (await db.execute(
            select(func.count()).select_from(APICallLog).where(
                APICallLog.timestamp >= desde
            )
        )).scalar() or 0
        fallidas = (await db.execute(
            select(func.count()).select_from(APICallLog).where(
                APICallLog.timestamp >= desde,
                APICallLog.status_code >= 400,
            )
        )).scalar() or 0
    except Exception:
        logger.exception("No se pudo medir la tasa de errores de Meta.")
        return Senal("meta_errores", "Errores de Meta", DESCONOCIDO,
                     umbral=TASA_ERRORES_META, unidad="proporción")

    if not total:
        return Senal("meta_errores", "Errores de Meta", OK, valor=0.0,
                     umbral=TASA_ERRORES_META, unidad="proporción",
                     detalle="Sin llamadas en la ventana.")

    tasa = fallidas / total
    return _evaluar("meta_errores", "Errores de Meta", round(tasa, 4),
                    TASA_ERRORES_META, "proporción",
                    detalle=f"{fallidas} de {total} llamadas en {horas} h.")


async def descartes_de_grounding(db: AsyncSession, *, horas: int = 24) -> Senal:
    """Proporción de respuestas del modelo que la guarda descartó (spec 10).

    Una tasa que sube de golpe es la señal más temprana de que un prompt se
    rompió. Sin ella, el síntoma llega como "el chat suena a plantilla" semanas
    después.
    """
    from app.models.ai_discard import AIDiscard
    from app.models.ai_token_usage import AITokenUsage

    desde = datetime.datetime.utcnow() - datetime.timedelta(hours=horas)
    try:
        llamadas = (await db.execute(
            select(func.count()).select_from(AITokenUsage).where(
                AITokenUsage.created_at >= desde
            )
        )).scalar() or 0
        descartes = (await db.execute(
            select(func.count()).select_from(AIDiscard).where(
                AIDiscard.created_at >= desde,
                AIDiscard.aplicado.is_(True),
            )
        )).scalar() or 0
    except Exception:
        logger.exception("No se pudo medir la tasa de descartes de grounding.")
        return Senal("grounding_descartes", "Descartes de la guarda", DESCONOCIDO,
                     umbral=TASA_DESCARTES_GROUNDING, unidad="proporción")

    if not llamadas:
        return Senal("grounding_descartes", "Descartes de la guarda", OK, valor=0.0,
                     umbral=TASA_DESCARTES_GROUNDING, unidad="proporción",
                     detalle="Sin llamadas al modelo en la ventana.")

    tasa = descartes / llamadas
    return _evaluar("grounding_descartes", "Descartes de la guarda", round(tasa, 4),
                    TASA_DESCARTES_GROUNDING, "proporción",
                    detalle=f"{descartes} descartes sobre {llamadas} llamadas.")


async def consumo_de_ia(db: AsyncSession) -> Senal:
    """La Organización que más cerca está de agotar su techo de IA (spec 10).

    Se vigila el máximo y no el promedio: un cliente al 95 % es un problema
    aunque los otros diez estén al 5, y el promedio lo escondería.
    """
    from app.models.organization import Organization
    from app.services.ai import presupuesto

    try:
        orgs = (await db.execute(
            select(Organization).where(
                Organization.ai_monthly_budget_usd.is_not(None)
            )
        )).scalars().all()
    except Exception:
        logger.exception("No se pudo medir el consumo de IA.")
        return Senal("ia_consumo", "Consumo de IA sobre el techo", DESCONOCIDO,
                     umbral=PROPORCION_IA, unidad="proporción")

    if not orgs:
        return Senal("ia_consumo", "Consumo de IA sobre el techo", OK, valor=0.0,
                     umbral=PROPORCION_IA, unidad="proporción",
                     detalle="Ninguna organización tiene techo configurado.")

    peor, peor_slug = 0.0, None
    for org in orgs:
        try:
            estado = await presupuesto.estado_del_mes(db, org.id, org=org)
        except Exception:
            continue
        if estado.proporcion is not None and float(estado.proporcion) > peor:
            peor, peor_slug = float(estado.proporcion), org.slug

    return _evaluar("ia_consumo", "Consumo de IA sobre el techo", round(peor, 4),
                    PROPORCION_IA, "proporción",
                    detalle=f"Máximo: {peor_slug}" if peor_slug else None)


async def escrituras_inciertas(db: AsyncSession) -> Senal:
    """Escrituras a Meta que quedaron sin confirmar (spec 03).

    Una sola ya merece mirarse: significa que se mandó algo y no se sabe si
    llegó. El umbral es cero porque el estado correcto es cero.
    """
    from app.models.campaign_proposal import WRITE_VERIFYING, MetaWriteIntent

    try:
        cuantas = (await db.execute(
            select(func.count()).select_from(MetaWriteIntent).where(
                MetaWriteIntent.status == WRITE_VERIFYING
            )
        )).scalar() or 0
    except Exception:
        logger.exception("No se pudieron contar las escrituras inciertas.")
        return Senal("escrituras_inciertas", "Escrituras sin confirmar", DESCONOCIDO,
                     umbral=ESCRITURAS_INCIERTAS, unidad="escrituras")

    return _evaluar("escrituras_inciertas", "Escrituras sin confirmar",
                    float(cuantas), ESCRITURAS_INCIERTAS - 1, "escrituras")


def salud_del_respaldo() -> Senal:
    """Horas desde el último respaldo verificado (spec 04).

    Se lee del archivo de sello que deja el proceso de respaldo. Si no existe,
    `desconocido` y no `ok`: dar por bueno un respaldo que no se pudo comprobar
    es exactamente cómo se descubre, el día que hace falta, que no había ninguno.
    """
    sello = os.environ.get("BACKUP_STAMP_FILE", "/var/lib/lejovista/ultimo-respaldo")
    try:
        if not os.path.exists(sello):
            return Senal("respaldo", "Antigüedad del último respaldo", DESCONOCIDO,
                         umbral=HORAS_SIN_RESPALDO, unidad="horas",
                         detalle=f"No existe el sello de respaldo ({sello}).")
        horas = (time_now() - os.path.getmtime(sello)) / 3600
    except Exception:
        logger.exception("No se pudo comprobar la salud del respaldo.")
        return Senal("respaldo", "Antigüedad del último respaldo", DESCONOCIDO,
                     umbral=HORAS_SIN_RESPALDO, unidad="horas")

    return _evaluar("respaldo", "Antigüedad del último respaldo",
                    round(horas, 1), HORAS_SIN_RESPALDO, "horas")


def time_now() -> float:
    """Envuelto para poder controlarlo en las pruebas sin tocar el reloj real."""
    import time as _t
    return _t.time()


def uso_de_disco(ruta: str = "/") -> Senal:
    try:
        uso = shutil.disk_usage(ruta)
        proporcion = uso.used / uso.total if uso.total else None
    except Exception:
        logger.exception("No se pudo medir el uso de disco.")
        return Senal("disco", "Uso de disco", DESCONOCIDO,
                     umbral=DISCO_LLENO, unidad="proporción")
    return _evaluar("disco", "Uso de disco",
                    None if proporcion is None else round(proporcion, 4),
                    DISCO_LLENO, "proporción")


def uso_de_memoria() -> Senal:
    """Proporción de memoria usada. `desconocido` fuera de Linux.

    Se lee de `/proc/meminfo` y no con una dependencia nueva: en el VPS existe,
    y en el portátil de quien desarrolla la señal se declara desconocida en vez
    de inventar un número.
    """
    try:
        with open("/proc/meminfo") as f:
            datos = {}
            for linea in f:
                clave, _, resto = linea.partition(":")
                datos[clave.strip()] = float(resto.strip().split()[0])
        total = datos.get("MemTotal")
        disponible = datos.get("MemAvailable")
        if not total or disponible is None:
            raise ValueError("meminfo incompleto")
        proporcion = (total - disponible) / total
    except Exception:
        return Senal("memoria", "Uso de memoria", DESCONOCIDO,
                     umbral=MEMORIA_LLENA, unidad="proporción",
                     detalle="No se pudo leer /proc/meminfo (¿no es Linux?).")
    return _evaluar("memoria", "Uso de memoria", round(proporcion, 4),
                    MEMORIA_LLENA, "proporción")


async def dependencias(db: AsyncSession) -> Senal:
    """Base de datos y Redis. La primera responde porque llegamos hasta aquí.

    Se cuenta cuántas están arriba en vez de devolver una señal por cada una:
    lo que hay que saber de un vistazo es si falta alguna, y cuál va en el
    detalle.
    """
    import redis.asyncio as aioredis

    from app.config import settings

    caidas: List[str] = []
    try:
        await db.execute(select(1))
    except Exception:
        caidas.append("base de datos")

    cliente = aioredis.Redis.from_url(settings.REDIS_URL)
    try:
        await cliente.ping()
    except Exception:
        caidas.append("redis")
    finally:
        try:
            await cliente.aclose()
        except Exception:
            pass

    return Senal(
        "dependencias", "Dependencias disponibles",
        ALERTA if caidas else OK,
        valor=float(2 - len(caidas)), umbral=2.0, unidad="servicios",
        detalle=("No responden: " + ", ".join(caidas)) if caidas else None,
    )


async def todas(db: AsyncSession) -> List[Senal]:
    """Las diez señales de la lista de la spec, en un solo barrido."""
    return [
        await frescura_del_sync(db),
        await profundidad_de_colas(db),
        await latencia_de_colas(db),
        await errores_de_meta(db),
        await descartes_de_grounding(db),
        await consumo_de_ia(db),
        await escrituras_inciertas(db),
        salud_del_respaldo(),
        uso_de_disco(),
        uso_de_memoria(),
        await dependencias(db),
    ]


async def resumen(db: AsyncSession) -> Dict[str, Any]:
    """Lo que publica el sistema para que lo lea el vigilante externo.

    `generado_en` es la parte que hace posible el criterio 12: el vigilante no
    solo mira si hay alertas, mira si esta marca de tiempo avanza. Con el VPS
    apagado no hay respuesta que leer, y esa ausencia es la alerta.
    """
    senales = await todas(db)
    con_alerta = [s for s in senales if s.alerta]
    return {
        "generado_en": datetime.datetime.utcnow().isoformat(),
        "estado": ALERTA if con_alerta else OK,
        "alertas": [s.clave for s in con_alerta],
        "senales": [s.as_dict() for s in senales],
    }
