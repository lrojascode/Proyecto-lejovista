from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.campaign_schemas import ChatDecisionRequest, ChatMessageRequest, ChatSessionCreateRequest
from app.schemas.response_schemas import ChatSessionResponse, ChatSessionSummary
from app.services.campaign_chat import engine
from app.services.campaign_chat.engine import ChatGuardError, ChatSessionNotFoundError
from app.utils.security import get_active_ad_account_id, get_current_user, has_role

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
    if requested_mode == "BUILD" and not await has_role(db, current_user, "OPERATOR"):
        raise HTTPException(
            status_code=403,
            detail="Esta acción requiere el rol OPERATOR o superior en la organización.",
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
