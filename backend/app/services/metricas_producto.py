"""Las métricas de producto que la spec maestra declara (spec 13, 15-17).

Seis cifras que responden si el producto cumple su promesa: ahorro operativo
para la agencia y beneficio publicitario para el cliente. Sin ellas no hay forma
de demostrar ninguna de las dos con el primer cliente, que es de lo que depende
que haya un segundo.

**Se derivan de eventos que el flujo ya produce.** No hace falta instrumentar
nada nuevo: la versión de propuesta lleva su momento, la aprobación el suyo, el
registro de cambios humanos dice si hubo correcciones y el `outcome_json` trae
el veredicto. Lo que faltaba era poder consultarlo.

**Se calculan al consultar, no se agregan a diario.** Con un cliente y decenas
de campañas por mes, una tabla de agregados sería más código que dato y otra
cosa que puede quedarse desincronizada. Si el volumen crece, este módulo es el
sitio donde ponerle una caché.
"""
import datetime
import logging
import statistics
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("lejovista.metricas")


def _minutos(desde: datetime.datetime, hasta: datetime.datetime) -> float:
    return (hasta - desde).total_seconds() / 60


async def tiempo_hasta_borrador(
    db: AsyncSession, organization_id: str, desde: datetime.datetime,
) -> Dict[str, Any]:
    """Minutos entre abrir la conversación y tener una propuesta lista.

    Es la métrica del ahorro operativo: lo que antes tardaba una persona
    armando la campaña a mano. Se mide sobre la SESIÓN de chat, que es donde
    empieza de verdad — medir desde la primera versión de propuesta mediría el
    tramo que el sistema ya hace solo.
    """
    from app.models.campaign_chat_session import CampaignChatSession
    from app.models.campaign_proposal import CampaignProposalVersion

    sesiones = (await db.execute(
        select(CampaignChatSession).where(
            CampaignChatSession.organization_id == organization_id,
            CampaignChatSession.created_at >= desde,
            CampaignChatSession.draft_id.is_not(None),
        )
    )).scalars().all()

    tiempos: List[float] = []
    for sesion in sesiones:
        primera = (await db.execute(
            select(func.min(CampaignProposalVersion.created_at)).where(
                CampaignProposalVersion.draft_id == sesion.draft_id
            )
        )).scalar_one_or_none()
        if primera and sesion.created_at and primera >= sesion.created_at:
            tiempos.append(_minutos(sesion.created_at, primera))

    return _resumen_de_tiempos(tiempos, "minutos")


async def tiempo_de_revision(
    db: AsyncSession, organization_id: str, desde: datetime.datetime,
) -> Dict[str, Any]:
    """Minutos entre la primera propuesta y su aprobación.

    Es lo que tarda una persona en revisar. Junto con la tasa de aprobación sin
    cambios dice si el sistema propone bien: mucho tiempo y muchas correcciones
    es una propuesta que hay que rehacer; poco tiempo y ninguna corrección
    podría ser confianza o podría ser que nadie está revisando.
    """
    from app.models.campaign_proposal import CampaignApproval, CampaignProposalVersion

    aprobaciones = (await db.execute(
        select(CampaignApproval).where(
            CampaignApproval.organization_id == organization_id,
            CampaignApproval.approved_at >= desde,
        )
    )).scalars().all()

    tiempos: List[float] = []
    for aprobacion in aprobaciones:
        primera = (await db.execute(
            select(func.min(CampaignProposalVersion.created_at)).where(
                CampaignProposalVersion.draft_id == aprobacion.draft_id
            )
        )).scalar_one_or_none()
        if primera and aprobacion.approved_at and aprobacion.approved_at >= primera:
            tiempos.append(_minutos(primera, aprobacion.approved_at))

    return _resumen_de_tiempos(tiempos, "minutos")


def _resumen_de_tiempos(tiempos: List[float], unidad: str) -> Dict[str, Any]:
    """Mediana además de promedio, y las dos con la muestra a la vista.

    La mediana porque una revisión que quedó abierta un fin de semana desplaza
    el promedio y no describe nada. La muestra porque una mediana sobre dos
    casos no es una mediana.
    """
    if not tiempos:
        return {"muestra": 0, "mediana": None, "promedio": None, "unidad": unidad}
    return {
        "muestra": len(tiempos),
        "mediana": round(statistics.median(tiempos), 1),
        "promedio": round(statistics.fmean(tiempos), 1),
        "unidad": unidad,
    }


async def aprobacion_sin_cambios(
    db: AsyncSession, organization_id: str, desde: datetime.datetime,
) -> Dict[str, Any]:
    """Qué proporción de propuestas se aprobó tal cual salió.

    Se mide contando las que NO tienen ningún cambio humano registrado
    (spec 11). Contar versiones no serviría: una versión puede crearse por un
    cambio no material que no corrigió nada.
    """
    from app.models.campaign_proposal import CampaignApproval
    from app.models.proposal_evidence import ProposalChange

    aprobados = (await db.execute(
        select(CampaignApproval.draft_id).where(
            CampaignApproval.organization_id == organization_id,
            CampaignApproval.approved_at >= desde,
        ).distinct()
    )).scalars().all()

    if not aprobados:
        return {"muestra": 0, "sin_cambios": 0, "proporcion": None}

    con_cambios = set((await db.execute(
        select(ProposalChange.draft_id).where(
            ProposalChange.organization_id == organization_id,
            ProposalChange.draft_id.in_(aprobados),
        ).distinct()
    )).scalars().all())

    sin_cambios = len([d for d in aprobados if d not in con_cambios])
    return {
        "muestra": len(aprobados),
        "sin_cambios": sin_cambios,
        "proporcion": round(sin_cambios / len(aprobados), 4),
    }


async def errores_bloqueados_antes_de_meta(
    db: AsyncSession, organization_id: str, desde: datetime.datetime,
) -> Dict[str, Any]:
    """Cuántas veces el sistema paró algo ANTES de que llegara a Meta.

    Es la métrica que justifica las compuertas: cada una es un error que el
    cliente no pagó. Se cuentan los descartes de la guarda del modelo y las
    intenciones de escritura que nunca se confirmaron por fallo previo.

    NO se cuentan los rechazos de validación de formulario: son errores de
    tecleo, no cosas que habrían llegado a Meta.
    """
    from app.models.ai_discard import AIDiscard
    from app.models.campaign_proposal import WRITE_PENDING, MetaWriteIntent

    try:
        descartes = (await db.execute(
            select(func.count()).select_from(AIDiscard).where(
                AIDiscard.organization_id == organization_id,
                AIDiscard.created_at >= desde,
                AIDiscard.aplicado.is_(True),
            )
        )).scalar() or 0
    except Exception:
        logger.exception("No se pudieron contar los descartes de la guarda.")
        descartes = 0

    try:
        intenciones_falladas = (await db.execute(
            select(func.count()).select_from(MetaWriteIntent).where(
                MetaWriteIntent.organization_id == organization_id,
                MetaWriteIntent.created_at >= desde,
                MetaWriteIntent.status == WRITE_PENDING,
                MetaWriteIntent.last_error.is_not(None),
            )
        )).scalar() or 0
    except Exception:
        logger.exception("No se pudieron contar las escrituras detenidas.")
        intenciones_falladas = 0

    return {
        "total": int(descartes + intenciones_falladas),
        "por_la_guarda_del_modelo": int(descartes),
        "escrituras_detenidas": int(intenciones_falladas),
    }


async def diferencia_frente_al_baseline(
    db: AsyncSession, organization_id: str, desde: datetime.datetime,
) -> Dict[str, Any]:
    """Cuánto más barata o más cara es una campaña del sistema que la referencia.

    Es la métrica del beneficio publicitario. Se compara contra el baseline
    CONGELADO de cada campaña (spec 11), no contra el vigente: juzgarlas contra
    la referencia de hoy las premiaría o castigaría por lo que hicieron otras
    campañas después.

    Negativo es bueno: significa que costó menos que la referencia.
    """
    from app.models.campaign_learning_log import CampaignLearningLog

    logs = (await db.execute(
        select(CampaignLearningLog).where(
            CampaignLearningLog.organization_id == organization_id,
            CampaignLearningLog.created_at >= desde,
            CampaignLearningLog.outcome_json.is_not(None),
        )
    )).scalars().all()

    diferencias: List[float] = []
    for log in logs:
        outcome = log.outcome_json or {}
        real = outcome.get("cost_per_conv")
        referencia = (outcome.get("baseline_congelado") or {}).get("mediana")
        if real and referencia and referencia > 0:
            diferencias.append((real - referencia) / referencia)

    if not diferencias:
        return {"muestra": 0, "diferencia_mediana": None, "unidad": "proporción"}

    return {
        "muestra": len(diferencias),
        "diferencia_mediana": round(statistics.median(diferencias), 4),
        "mejores_que_el_baseline": len([d for d in diferencias if d < 0]),
        "unidad": "proporción",
    }


async def uso_del_chat(
    db: AsyncSession, organization_id: str, desde: datetime.datetime,
) -> Dict[str, Any]:
    """Sesiones, turnos y cuántas terminaron en una campaña.

    La comprensión de las respuestas no se puede medir desde aquí —la spec la
    deja como verificación con el cliente piloto— así que no se inventa un
    proxy. Lo que sí dice algo es si el chat se usa y si lleva a algún lado.
    """
    from app.models.campaign_chat_session import CampaignChatSession

    sesiones = (await db.execute(
        select(CampaignChatSession).where(
            CampaignChatSession.organization_id == organization_id,
            CampaignChatSession.created_at >= desde,
        )
    )).scalars().all()

    if not sesiones:
        return {"sesiones": 0, "turnos_totales": 0, "turnos_mediana": None,
                "terminaron_en_campana": 0, "por_modo": {}}

    turnos = [s.turn_count or 0 for s in sesiones]
    por_modo: Dict[str, int] = {}
    for s in sesiones:
        modo = s.mode or "BUILD"
        por_modo[modo] = por_modo.get(modo, 0) + 1

    return {
        "sesiones": len(sesiones),
        "turnos_totales": sum(turnos),
        "turnos_mediana": round(statistics.median(turnos), 1),
        "terminaron_en_campana": len([s for s in sesiones if s.draft_id]),
        "por_modo": por_modo,
    }


async def todas(
    db: AsyncSession, organization_id: str, *, dias: int = 30,
) -> Dict[str, Any]:
    """Las seis métricas declaradas, para una Organización y un periodo."""
    desde = datetime.datetime.utcnow() - datetime.timedelta(days=max(1, dias))
    return {
        "dias": dias,
        "desde": desde.isoformat(),
        "tiempo_hasta_borrador": await tiempo_hasta_borrador(db, organization_id, desde),
        "tiempo_de_revision": await tiempo_de_revision(db, organization_id, desde),
        "aprobacion_sin_cambios": await aprobacion_sin_cambios(db, organization_id, desde),
        "errores_bloqueados_antes_de_meta": await errores_bloqueados_antes_de_meta(
            db, organization_id, desde,
        ),
        "diferencia_frente_al_baseline": await diferencia_frente_al_baseline(
            db, organization_id, desde,
        ),
        "uso_del_chat": await uso_del_chat(db, organization_id, desde),
    }
