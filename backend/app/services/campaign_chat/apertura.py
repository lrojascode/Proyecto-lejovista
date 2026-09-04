"""Lo que el chat dice al abrirse, sin que le pregunten (spec 12, 1-2 y 17).

Junta las tres piezas que produce la apertura —resumen, avisos y contexto— y las
persiste como los primeros mensajes de la sesión.

**Todo determinista.** El resumen se construye con datos ya sincronizados y los
avisos salen de los `AIInsight` que el análisis ya produjo. Si el modelo no está,
la apertura es idéntica: es el criterio 2, y es la diferencia entre un producto
que degrada y uno que se queda en blanco.

**No dispara análisis.** Se consume lo que el análisis agendado dejó. Abrir una
conversación no puede provocar trabajo pesado ni saltarse el debounce de la
ADR-07: si cada apertura disparara un análisis, veinte aperturas en una mañana
serían veinte análisis de la misma cuenta.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_insight import AIInsight
from app.models.chat_notice import ChatNoticeLog
from app.services.baseline_repository import baseline_de_la_cuenta, campanas_agregadas
from app.services.campaign_chat import avisos as avisos_mod
from app.services.campaign_chat import cards, contexto as contexto_mod
from app.services.campaign_chat import resumen as resumen_mod

logger = logging.getLogger("lejovista.chat.apertura")

# Cuántos días atrás se leen los Insight. Un hallazgo de hace un mes describe
# una cuenta que ya no existe, y entregarlo al abrir el chat manda a revisar
# algo que probablemente ya se resolvió solo.
DIAS_DE_INSIGHTS = 7


async def _entregados_recientemente(
    db: AsyncSession, organization_id: str, ad_account_id: str, user_id: Optional[str],
) -> Dict[str, datetime.datetime]:
    """La última entrega de cada huella, para este usuario y esta cuenta.

    Por usuario a propósito: dos personas de la misma organización tienen
    derecho a ver el aviso cada una. Silenciarlo para la segunda porque la
    primera ya lo vio esconde el hallazgo justo de quien podría actuar.
    """
    filas = (await db.execute(
        select(ChatNoticeLog.huella, ChatNoticeLog.entregado_en).where(
            ChatNoticeLog.organization_id == organization_id,
            ChatNoticeLog.ad_account_id == ad_account_id,
            ChatNoticeLog.user_id == user_id,
        )
    )).all()

    ultimo: Dict[str, datetime.datetime] = {}
    for huella, entregado_en in filas:
        if entregado_en is None:
            continue
        if huella not in ultimo or entregado_en > ultimo[huella]:
            ultimo[huella] = entregado_en
    return ultimo


async def construir_apertura(
    db: AsyncSession,
    ad_account: Any,
    *,
    organization_id: str,
    user_id: Optional[str],
    organization: Any = None,
    ahora: Optional[datetime.datetime] = None,
    hoy: Optional[datetime.date] = None,
) -> Tuple[List[Dict[str, Any]], List[avisos_mod.Aviso]]:
    """Los mensajes de apertura y los avisos que se entregaron.

    Devuelve los avisos además de los mensajes porque el llamador tiene que
    registrarlos: sin ese registro el límite de frecuencia no existe y el mismo
    aviso saldría en cada apertura.
    """
    ahora = ahora or datetime.datetime.utcnow()
    hoy = hoy or datetime.date.today()

    frescura = contexto_mod.frescura_de(ad_account, ahora=ahora)

    campanas = await campanas_agregadas(
        db, ad_account.id,
        desde=hoy - datetime.timedelta(days=resumen_mod.VENTANA_DIAS),
        hasta=hoy,
    )
    baseline = await baseline_de_la_cuenta(db, ad_account.id, organization=organization)
    resumen = resumen_mod.construir(campanas, baseline=baseline)

    ctx = contexto_mod.ContextoDeRespuesta(
        cuenta_id=ad_account.id,
        cuenta_nombre=getattr(ad_account, "name", None),
        frescura=frescura,
        suficiencia=(
            contexto_mod.suficiente()
            if resumen.hay_actividad else
            contexto_mod.insuficiente(
                "Todavía no hay actividad suficiente en este periodo para sacar "
                "conclusiones."
            )
        ),
        **contexto_mod.periodo_desde_dias(
            resumen.ventana_dias, f"los últimos {resumen.ventana_dias} días", hoy=hoy,
        ),
    )

    mensajes = [
        cards.text_message_con_contexto(
            resumen_mod.redactar(resumen),
            contexto=ctx,
            quick_replies=resumen_mod.respuestas_rapidas(resumen),
            origen_de_la_respuesta="determinista",
        )
    ]
    mensajes[0]["resumen"] = resumen.as_dict()

    insights = (await db.execute(
        select(AIInsight).where(
            AIInsight.ad_account_id == ad_account.id,
            AIInsight.organization_id == organization_id,
            AIInsight.is_dismissed.is_(False),
            AIInsight.created_at >= ahora - datetime.timedelta(days=DIAS_DE_INSIGHTS),
        )
    )).scalars().all()

    entregados = await _entregados_recientemente(
        db, organization_id, ad_account.id, user_id,
    )
    a_entregar, descartados = avisos_mod.seleccionar(insights, entregados, ahora=ahora)

    if descartados:
        logger.info(
            "Apertura de chat (cuenta %s): %d aviso(s) entregado(s), %d silenciado(s) "
            "por frecuencia.", ad_account.id, len(a_entregar), len(descartados),
        )

    texto_avisos = avisos_mod.redactar(a_entregar)
    if texto_avisos:
        mensajes.append(cards.avisos_message(texto_avisos, a_entregar))

    return mensajes, a_entregar


async def registrar_entrega(
    db: AsyncSession,
    avisos: List[avisos_mod.Aviso],
    *,
    organization_id: str,
    ad_account_id: str,
    user_id: Optional[str],
    ahora: Optional[datetime.datetime] = None,
) -> None:
    """Anota qué se entregó, que es lo que hace cumplir el límite."""
    ahora = ahora or datetime.datetime.utcnow()
    for aviso in avisos:
        db.add(ChatNoticeLog(
            organization_id=organization_id,
            ad_account_id=ad_account_id,
            user_id=user_id,
            huella=aviso.huella,
            categoria=aviso.categoria,
            nivel=aviso.nivel,
            insight_id=aviso.insight_id,
            entregado_en=ahora,
        ))
