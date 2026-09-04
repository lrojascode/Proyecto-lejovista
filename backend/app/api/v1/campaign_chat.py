from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.tenant_rules.modules import is_module_enabled
from app.schemas.campaign_schemas import ChatDecisionRequest, ChatMessageRequest, ChatSessionCreateRequest
from app.schemas.response_schemas import ChatSessionResponse, ChatSessionSummary
from app.services.campaign_chat import engine
from app.services.campaign_chat.engine import ChatGuardError, ChatSessionNotFoundError
from app.services.campaign_chat.medios_contexto import CorreccionInvalida
from app.utils.security import (
    get_active_ad_account_id, get_current_user, has_role, require_agency,
)

router = APIRouter(prefix="/campaigns/chat", tags=["campaign-chat"])


@router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_chat_session(
    request: ChatSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    active_ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db),
):
    """Arranca una conversación nueva en modo BUILD o ASK."""
    requested_mode = request.mode or "BUILD"
    if requested_mode == "BUILD":
        # Dos llaves distintas y las dos hacen falta: el ROL dice si esta
        # persona puede crear campañas, y el MÓDULO dice si la organización
        # contrató la creación por chat. Un OPERATOR de una org sin el módulo
        # sigue pudiendo consultar (modo ASK), que es el punto de separarlos.
        if not await has_role(db, current_user, "OPERATOR"):
            raise HTTPException(
                status_code=403,
                detail="Esta acción requiere el rol OPERATOR o superior en la organización.",
            )
        org = await db.get(Organization, current_user.organization_id)
        if not org or not is_module_enabled(org, "chat_creacion"):
            raise HTTPException(
                status_code=403,
                detail="La creación de campañas por chat no está habilitada para tu organización.",
            )
    try:
        session = await engine.create_session(
            db, current_user, request.ad_account_id or active_ad_account_id, mode=requested_mode
        )
    except ChatGuardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return engine.session_to_dict(session)


@router.get("/sessions", response_model=List[ChatSessionSummary])
async def list_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = await engine.list_sessions(db, current_user)
    return [
        {
            "id": s.id, "state": s.state, "status": s.status, "mode": s.mode or "BUILD",
            "ad_account_id": s.ad_account_id,
            "draft_id": s.draft_id, "updated_at": s.updated_at.isoformat() if s.updated_at else "",
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        session, messages = await engine.get_session_view(db, current_user, session_id)
    except ChatSessionNotFoundError:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")
    return engine.session_to_dict(session, messages)


@router.post("/sessions/{session_id}/messages", response_model=ChatSessionResponse)
async def post_chat_message(
    session_id: str,
    request: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        session = await engine.load_session(db, current_user, session_id)
    except ChatSessionNotFoundError:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")
    session = await engine.process_message(
        db, current_user, session,
        client_turn_id=request.client_turn_id, text=request.text,
        image_path=request.image_path, video_path=request.video_path,
    )
    return engine.session_to_dict(session)


@router.post("/sessions/{session_id}/decision", response_model=ChatSessionResponse)
async def post_chat_decision(
    session_id: str,
    request: ChatDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        session = await engine.load_session(db, current_user, session_id)
    except ChatSessionNotFoundError:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")
    session = await engine.process_decision(
        db, current_user, session,
        client_turn_id=request.client_turn_id, card_id=request.card_id,
        decision=request.decision, text=request.text,
    )
    return engine.session_to_dict(session)


@router.post("/sessions/{session_id}/advance", response_model=ChatSessionResponse)
async def advance_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Relee el estado real de la Saga (`CampaignDraft`) y lo persiste en la
    conversación — es lo que el frontend llama en su polling de ~2.5s mientras
    la campaña se está creando (fallback si el WebSocket se cae)."""
    try:
        session = await engine.advance_session(db, current_user, session_id)
    except ChatSessionNotFoundError:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")
    return engine.session_to_dict(session)


class CorreccionDelMedioRequest(BaseModel):
    """Lo que el cliente corrige de lo que el sistema entendió del medio."""

    client_turn_id: str
    producto: Optional[str] = None
    descripcion: Optional[str] = None
    texto_visible: Optional[str] = None
    elementos: Optional[List[str]] = None


@router.post("/sessions/{session_id}/media-context", response_model=ChatSessionResponse)
async def corregir_contexto_del_medio(
    session_id: str,
    request: CorreccionDelMedioRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """El cliente corrige lo que el sistema entendió de la foto o el video.

    Es la otra mitad del criterio 10 de la spec 12: mostrar lo extraído sirve de
    poco si no se puede corregir. La corrección MANDA sobre lo extraído y llega
    a la propuesta — si el cliente dice que el producto es otro, el texto del
    anuncio tiene que hablar de ese.
    """
    try:
        session = await engine.load_session(db, current_user, session_id)
    except ChatSessionNotFoundError:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")

    correcciones = {
        campo: valor
        for campo, valor in (
            ("producto", request.producto),
            ("descripcion", request.descripcion),
            ("texto_visible", request.texto_visible),
            ("elementos", request.elementos),
        )
        if valor is not None
    }
    if not correcciones:
        raise HTTPException(status_code=400, detail={
            "code": "NOTHING_TO_CORRECT",
            "message": "No mandaste ninguna corrección.",
        })

    try:
        session = await engine.process_media_correction(
            db, current_user, session,
            client_turn_id=request.client_turn_id, correcciones=correcciones,
        )
    except CorreccionInvalida as e:
        raise HTTPException(status_code=400, detail=e.as_detail())

    return engine.session_to_dict(session)


class ResumenDelChatResponse(BaseModel):
    dias: int
    sesiones_abiertas: int
    con_resumen_entregado: int
    avisos_entregados: int
    avisos_silenciados_por_frecuencia: int
    briefs_completados: int
    briefs_abandonados: int
    respuestas_deterministas: int


@router.get("/observabilidad", response_model=ResumenDelChatResponse)
async def observabilidad_del_chat(
    dias: int = 30,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Si el chat está sirviendo, y si los avisos se han vuelto ruido (spec 12).

    Cuatro cosas que no se pueden ver desde dentro de una conversación:

    * **Aperturas con resumen entregado.** Si baja, el resumen está fallando y
      el cliente vuelve a encontrarse el cursor.
    * **Avisos entregados frente a silenciados por frecuencia.** Es lo único que
      dice si la ventana está bien puesta: si se silencia casi todo, ahoga; si
      no se silencia nada, no está haciendo nada.
    * **Briefs completados frente a abandonados.** La recogida del brief es la
      pieza con más riesgo de fricción, y esta es la cifra que decide si hay que
      revertirla.
    * **Respuestas deterministas.** Cuántas veces el modelo no estuvo.
    """
    import datetime as _dt

    from sqlalchemy import func as _func, select as _select

    from app.models.campaign_chat_session import CampaignChatSession
    from app.models.chat_notice import ChatNoticeLog
    from app.services.campaign_chat.brief import estado_del_brief

    desde = _dt.datetime.utcnow() - _dt.timedelta(days=max(1, dias))
    org_id = current_user.organization_id

    sesiones = (await db.execute(
        _select(CampaignChatSession).where(
            CampaignChatSession.organization_id == org_id,
            CampaignChatSession.created_at >= desde,
        )
    )).scalars().all()

    con_resumen = 0
    deterministas = 0
    completados = 0
    abandonados = 0
    for sesion in sesiones:
        mensajes = sesion.messages_json or []
        if any(m.get("resumen") for m in mensajes):
            con_resumen += 1
        deterministas += sum(
            1 for m in mensajes if m.get("origen") == "determinista"
        )
        if (sesion.mode or "BUILD") == "BUILD":
            if estado_del_brief(sesion.slots_json or {}).completo:
                completados += 1
            elif sesion.status != "OPEN":
                # Solo cuentan como abandonados los que ya no van a completarse.
                # Contar los abiertos mezclaría "todavía lo está contestando" con
                # "se fue", que son cosas distintas y se arreglan distinto.
                abandonados += 1

    entregados = (await db.execute(
        _select(_func.count()).select_from(ChatNoticeLog).where(
            ChatNoticeLog.organization_id == org_id,
            ChatNoticeLog.entregado_en >= desde,
        )
    )).scalar() or 0

    return ResumenDelChatResponse(
        dias=dias,
        sesiones_abiertas=len(sesiones),
        con_resumen_entregado=con_resumen,
        avisos_entregados=int(entregados),
        # Los silenciados no se persisten —serían una fila por apertura y por
        # hallazgo vigente, que crece sin aportar nada nuevo— así que se leen del
        # log de la aplicación. Se declara en cero aquí en vez de inventar un
        # número: mentir en la métrica que calibra la ventana la haría inútil.
        avisos_silenciados_por_frecuencia=0,
        briefs_completados=completados,
        briefs_abandonados=abandonados,
        respuestas_deterministas=deterministas,
    )
