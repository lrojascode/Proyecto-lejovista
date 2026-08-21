"""Motor del chat de creación de campañas (CH-1). Orquesta la máquina de
estados pura (`machine.py`), el extractor determinista (`extraction.py`) y
las tarjetas (`cards.py`) contra la base de datos: es la única capa que
conoce `CampaignChatSession`, hace queries y llama a los servicios
compartidos con el formulario (`build_campaign_proposal`,
`start_campaign_creation`, `resolve_destination`).

Principio rector: el chat NO abre un segundo camino de escritura. Todo
termina llamando a las mismas funciones que usa `POST /campaigns/` — nunca
construye su propio `CampaignDraft` ni su propio chain de Celery.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_chat_session import CampaignChatSession
from app.models.campaign_draft import CampaignDraft
from app.models.meta_page import MetaPage
from app.models.organization import Organization
from app.models.user import User
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.ai.llm import ChatPlan, OpenRouterClient
from app.services.ai.token_tracking import UsageContext
from app.services.campaign_chat import cards, extraction, facts as facts_module, questions, rephrase
from app.services.campaign_chat.machine import GATE_STATES, SessionFacts, invalidation_for_changed_slots, next_state
from app.services.campaign_chat.period import PERIOD_ENUM, Period, parse_period, period_from_enum
from app.services.campaign_chat.slots import sanitize_product_name, strip_accents

_COORDINATION_RE = re.compile(r"\b(y|ademas|además|tambien|también|y tambien|y también|junto con)\b", re.IGNORECASE)
from app.services.campaign_creation import (
    CAMPAIGN_SESSION_WINDOW_HOURS,
    MAX_CAMPAIGNS_PER_SESSION,
    CampaignCapReachedError,
    cap_reached,
    start_campaign_creation,
)
from app.services.campaign_destination import resolve_destination
from app.services.campaign_preflight import CampaignPreflight
from app.services.campaign_proposal import build_campaign_proposal
from app.services.campaign_risk import assess_campaign_risk
from app.tenant_rules.campaign_defaults import get_campaign_rules_for_org
from app.utils.security import has_role

logger = logging.getLogger(__name__)

# Topes de llamadas al LLM por sesión y por modo (H6)
MAX_UNDERSTANDING_CALLS: Dict[str, int] = {"BUILD": 12, "ASK": 40}
MAX_ANALYSIS_TURNS: Dict[str, int] = {"BUILD": 30, "ASK": 60}
MAX_MESSAGES_PERSISTED = 200

# Compatibilidad hacia atrás
MAX_LLM_TURNS_PER_SESSION = 12
MAX_REPHRASED_TURNS = 30


def _max_understanding_calls(session: CampaignChatSession) -> int:
    mode = getattr(session, "mode", "BUILD") or "BUILD"
    return MAX_UNDERSTANDING_CALLS.get(mode, 12)


def _max_analysis_turns(session: CampaignChatSession) -> int:
    mode = getattr(session, "mode", "BUILD") or "BUILD"
    return MAX_ANALYSIS_TURNS.get(mode, 30)

_SLOT_KEYS = {
    "goal_text", "product_name", "budget", "budget_type", "days",
    "image_path", "video_path", "page_id", "whatsapp_number",
    "headline", "description", "copy_text",
}
_APPLICABLE_EXTRACTED_KEYS = _SLOT_KEYS - {"page_id", "video_path", "image_path"}


class ChatGuardError(Exception):
    """Bloquea la creación de una sesión con un motivo accionable (sin cuenta,
    sin página con WhatsApp, tope de campañas ya alcanzado)."""


class ChatSessionNotFoundError(Exception):
    pass


# --- Creación de sesión ---

async def create_session(
    db: AsyncSession, current_user: User, ad_account_id: Optional[str], mode: str = "BUILD"
) -> CampaignChatSession:
    if not ad_account_id:
        raise ChatGuardError(
            "No hay ninguna cuenta de anuncios seleccionada. Elegí una cuenta antes de continuar."
        )

    q = select(AdAccount).where(
        AdAccount.id == ad_account_id,
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status != "ARCHIVED",
    )
    ad_account = (await db.execute(q)).scalar_one_or_none()
    if not ad_account:
        raise ChatGuardError("AdAccount no encontrada para esta organización.")

    if mode == "ASK":
        session = CampaignChatSession(
            organization_id=current_user.organization_id,
            created_by_user_id=current_user.id,
            ad_account_id=ad_account.id,
            state="BROWSING",
            status="OPEN",
            mode="ASK",
            slots_json={"_origin_mode": "ASK"},
            messages_json=[cards.text_message(
                "¡Hola! Soy tu analista de Meta Ads. ¿Qué querés saber sobre el rendimiento de tu cuenta?",
                quick_replies=["¿Cómo vengo este mes?", "¿Cuál campaña rinde mejor?", "¿Dónde estoy perdiendo plata?"]
            )],
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    if await cap_reached(db, current_user.organization_id, ad_account):
        org = await db.get(Organization, current_user.organization_id)
        rules = get_campaign_rules_for_org(org)
        limit = rules.get("max_campaigns_per_window", 15)
        window_hours = rules.get("campaign_window_hours", 24)
        raise ChatGuardError(
            f"Ya llegaste al máximo de {limit} campañas nuevas en las últimas "
            f"{window_hours}h (protección para que Meta no bloquee tu cuenta por "
            "crear demasiadas seguidas). Probá de nuevo más tarde."
        )

    page, destination_error = await resolve_destination(db, ad_account, current_user.organization_id)
    if page is None:
        raise ChatGuardError(destination_error or cards.GUARD_NO_PAGE_TEXT)

    session = CampaignChatSession(
        organization_id=current_user.organization_id,
        created_by_user_id=current_user.id,
        ad_account_id=ad_account.id,
        state="ASK_GOAL",
        status="OPEN",
        mode="BUILD",
        slots_json={"page_id": page.external_page_id, "whatsapp_number": page.whatsapp_number},
        messages_json=[cards.text_message(cards.GREETING_TEXT)],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def load_session(db: AsyncSession, current_user: User, session_id: str) -> CampaignChatSession:
    session = await db.get(CampaignChatSession, session_id)
    if not session or session.organization_id != current_user.organization_id:
        raise ChatSessionNotFoundError()
    return session


async def list_sessions(db: AsyncSession, current_user: User) -> List[CampaignChatSession]:
    q = (
        select(CampaignChatSession)
        .where(CampaignChatSession.organization_id == current_user.organization_id)
        .order_by(CampaignChatSession.updated_at.desc())
        .limit(50)
    )
    return list((await db.execute(q)).scalars().all())


# --- Lectura + reconciliación de la Saga ---

async def _materialize_creation_message(db: AsyncSession, session: CampaignChatSession) -> Optional[Dict[str, Any]]:
    """Deriva el mensaje de progreso/resultado/error a partir del estado REAL
    de `CampaignDraft` — nunca se confía en un valor cacheado, así que un
    WebSocket perdido nunca deja la conversación colgada: el próximo GET o
    `/advance` la pone al día leyendo la fuente de verdad."""
    if not session.draft_id:
        return None
    draft = await db.get(CampaignDraft, session.draft_id)
    if draft is None:
        return None
    if draft.status == "CREATED":
        result = (draft.payload or {}).get("result") or {}
        return cards.build_result_message(draft.id, result, draft.name)
    if draft.status == "FAILED":
        text = f"{cards.SAGA_FAILED_TEXT} {draft.error_message or 'motivo no especificado.'}".strip()
        return cards.build_error_message("SAGA_FAILED", text)
    return cards.build_progress_message(draft.id)


async def get_session_view(
    db: AsyncSession, current_user: User, session_id: str
) -> Tuple[CampaignChatSession, List[Dict[str, Any]]]:
    """Lectura pura: NO persiste. La materialización del progreso de la Saga se
    calcula al vuelo para esta respuesta; `advance_session` es quien la graba."""
    session = await load_session(db, current_user, session_id)
    messages = list(session.messages_json or [])
    if session.state == "CREATING" and session.status == "OPEN":
        extra = await _materialize_creation_message(db, session)
        if extra:
            messages.append(extra)
    return session, messages


async def advance_session(db: AsyncSession, current_user: User, session_id: str) -> CampaignChatSession:
    """Igual que `get_session_view`, pero persiste el mensaje nuevo y cierra la
    sesión si la Saga ya terminó — es lo que el frontend llama en su polling
    para no perder el resultado si el WebSocket se cayó."""
    session = await load_session(db, current_user, session_id)
    if session.state != "CREATING" or session.status != "OPEN":
        return session

    extra = await _materialize_creation_message(db, session)
    if extra is None:
        return session

    messages = list(session.messages_json or [])
    last_type = messages[-1]["type"] if messages else None
    if extra["type"] == "progress" and last_type == "progress":
        return session

    messages.append(extra)
    session.messages_json = messages[-MAX_MESSAGES_PERSISTED:]
    if extra["type"] == "result":
        session.status = "CLOSED_CREATED"
        session.state = "DONE"
    elif extra["type"] == "error":
        session.status = "CLOSED_FAILED"
        session.state = "FAILED"

    await db.commit()
    await db.refresh(session)
    return session


# --- Serialización ---

def session_to_dict(session: CampaignChatSession, messages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    slots_raw = session.slots_json or {}
    slots = {k: v for k, v in slots_raw.items() if k in _SLOT_KEYS}
    return {
        "id": session.id,
        "state": session.state,
        "status": session.status,
        "mode": session.mode or "BUILD",
        "ad_account_id": session.ad_account_id,
        "draft_id": session.draft_id,
        "slots": slots,
        "messages": messages if messages is not None else (session.messages_json or []),
        "created_at": session.created_at.isoformat() if session.created_at else "",
        "updated_at": session.updated_at.isoformat() if session.updated_at else "",
    }


# --- Slots y facts ---

def _facts(session: CampaignChatSession) -> SessionFacts:
    slots = session.slots_json or {}
    proposal = session.proposal_json or {}
    return SessionFacts(
        goal_text=slots.get("goal_text"),
        image_path=slots.get("image_path"),
        video_path=slots.get("video_path"),
        has_proposal=session.proposal_json is not None,
        proposal_accepted=bool(proposal.get("_accepted")),
        draft_id=session.draft_id,
        mode=session.mode or "BUILD",
    )


def _apply_slot_changes(session: CampaignChatSession, changes: Dict[str, Any]) -> None:
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        return
    slots = dict(session.slots_json or {})
    changed_keys = set()
    for key, value in changes.items():
        if slots.get(key) != value:
            changed_keys.add(key)
        slots[key] = value
    session.slots_json = slots
    if not changed_keys:
        return

    invalidation = invalidation_for_changed_slots(frozenset(changed_keys))
    if invalidation.clear_proposal:
        session.proposal_json = None
    elif invalidation.clear_acceptance and session.proposal_json is not None:
        proposal = dict(session.proposal_json)
        proposal["_accepted"] = False
        session.proposal_json = proposal
    if invalidation.clear_preflight:
        session.preflight_json = None
        session.risk_json = None


def _total_budget(slots: Dict[str, Any]) -> float:
    budget = slots.get("budget") or 0
    if slots.get("budget_type") == "DAILY":
        return round(budget * (slots.get("days") or 1), 2)
    return budget


def _unclear_text_for_state(state: str) -> str:
    if state == "ASK_MEDIA":
        return f"{cards.UNCLEAR_TEXT} {cards.ASK_MEDIA_TEXT}"
    if state == "ASK_GOAL":
        return f"{cards.UNCLEAR_TEXT} {cards.ASK_GOAL_TEXT}"
    return cards.UNCLEAR_TEXT


def _close_as_cancelled(session: CampaignChatSession, messages: List[Dict[str, Any]], text: str) -> None:
    messages.append(cards.text_message(text))
    session.status = "CLOSED_CANCELLED"
    session.state = "ABANDONED"


# --- Pasos automáticos (BUILD_PLAN, y volver a mostrar la tarjeta vigente) ---

async def _build_and_append_proposal(
    db: AsyncSession, current_user: User, session: CampaignChatSession, messages: List[Dict[str, Any]]
) -> bool:
    ad_account = await db.get(AdAccount, session.ad_account_id)
    slots = dict(session.slots_json or {})
    try:
        proposal = await build_campaign_proposal(
            db, current_user, ad_account,
            product_name=slots.get("product_name") or "Producto",
            image_path=slots.get("image_path"),
        )
    except Exception:
        logger.exception("No se pudo armar la propuesta de campaña (sesión %s)", session.id)
        messages.append(cards.build_error_message(
            "INTERNAL", "No pude armar tu campaña con esos datos. ¿Podés intentarlo de nuevo?",
        ))
        return False

    copy = proposal.get("copy") or {}
    suggested_prod = proposal.get("suggested_product_name") or copy.get("suggested_product_name")
    if suggested_prod and not slots.get("product_name"):
        slots["product_name"] = suggested_prod
        if not slots.get("goal_text"):
            slots["goal_text"] = suggested_prod

    slots.setdefault("budget", proposal.get("suggested_budget"))
    slots.setdefault("days", proposal.get("suggested_days"))
    slots.setdefault("budget_type", "LIFETIME")
    slots.setdefault("copy_text", (copy.get("primary_text") or [""])[0])
    slots.setdefault("headline", (copy.get("headline") or [None])[0])
    slots.setdefault("description", copy.get("description"))
    session.slots_json = slots

    proposal = dict(proposal)
    proposal["_accepted"] = False
    session.proposal_json = proposal
    return True


async def _run_auto_steps(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    messages: List[Dict[str, Any]], user_text: str = "",
) -> None:
    """Tras aplicar los cambios de un turno, corre lo que corresponda
    automáticamente (armar la propuesta) y muestra la tarjeta del nuevo
    estado. Nunca avanza sola hasta CONFIRM_RISK/CREATING — esas dos son
    compuertas que solo mueve una decisión explícita."""
    state = next_state(_facts(session))

    if state == "ASK_GOAL":
        session.state = state
        messages.append(await _say(db, current_user, session, cards.ASK_GOAL_TEXT, user_text))
        return
    if state == "ASK_MEDIA":
        session.state = state
        messages.append(await _say(db, current_user, session, cards.ASK_MEDIA_TEXT, user_text))
        return
    if state == "BUILD_PLAN":
        # BUILDING_TEXT no se reescribe: es el relleno que se muestra justo
        # antes del paso más lento del flujo, y no vale la pena sumarle otra
        # llamada de red a una espera que ya se siente.
        messages.append(cards.text_message(cards.BUILDING_TEXT))
        if not await _build_and_append_proposal(db, current_user, session, messages):
            session.state = "ASK_MEDIA" if not (session.slots_json or {}).get("image_path") else "ASK_GOAL"
            return
        state = next_state(_facts(session))

    if state == "REVIEW_PLAN":
        session.state = "REVIEW_PLAN"
        messages.append(cards.build_review_card(session.slots_json or {}, session.proposal_json or {}))
        return

    session.state = state


# --- Compuertas (aceptar / rechazar / cambiar) ---

async def _get_page(db: AsyncSession, current_user: User, page_id: Optional[str]) -> Optional[MetaPage]:
    if not page_id:
        return None
    q = select(MetaPage).where(
        MetaPage.organization_id == current_user.organization_id,
        MetaPage.external_page_id == page_id,
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _handle_accept_review(
    db: AsyncSession, current_user: User, session: CampaignChatSession, messages: List[Dict[str, Any]]
) -> None:
    if session.proposal_json is None:
        messages.append(cards.build_error_message("VALIDATION", "Todavía no tengo una propuesta para confirmar."))
        return

    proposal = dict(session.proposal_json)
    proposal["_accepted"] = True
    session.proposal_json = proposal

    slots = session.slots_json or {}
    ad_account = await db.get(AdAccount, session.ad_account_id)
    org = await db.get(Organization, current_user.organization_id)
    rules = get_campaign_rules_for_org(org)

    risk = assess_campaign_risk(
        budget=_total_budget(slots), copy_text=slots.get("copy_text") or "", suggestion=proposal,
    )
    session.risk_json = risk

    preflight = None
    try:
        page = await _get_page(db, current_user, slots.get("page_id"))
        preflight = await CampaignPreflight(ad_account, rules).run(
            product_name=slots.get("product_name") or "Producto",
            page_id=slots.get("page_id"),
            whatsapp_number=slots.get("whatsapp_number"),
            budget=slots.get("budget") or 0,
            days=slots.get("days") or 1,
            copy_text=slots.get("copy_text") or "",
            image_hash=slots.get("image_hash"),
            budget_type=slots.get("budget_type") or "LIFETIME",
            headline=slots.get("headline"),
            description=slots.get("description"),
            instagram_user_id=page.instagram_actor_id if page else None,
            campaign_name=proposal.get("suggested_campaign_name"),
            best_age_range=proposal.get("best_age_range"),
            best_device=proposal.get("best_device"),
            excluded_placements=proposal.get("excluded_placements") or [],
        )
    except Exception:
        logger.exception("Preflight falló para la sesión de chat %s", session.id)
    session.preflight_json = preflight

    session.state = "CONFIRM_RISK"
    messages.append(cards.build_risk_card(risk, preflight))


def _build_create_request(session: CampaignChatSession) -> CampaignCreateRequest:
    slots = session.slots_json or {}
    return CampaignCreateRequest(
        ad_account_id=session.ad_account_id,
        page_id=slots.get("page_id"),
        product_name=slots.get("product_name") or "Producto",
        budget=slots.get("budget") or 0,
        budget_type=slots.get("budget_type") or "LIFETIME",
        days=slots.get("days") or 1,
        whatsapp_number=slots.get("whatsapp_number") or "",
        image_path=slots.get("image_path"),
        video_path=slots.get("video_path"),
        copy_text=slots.get("copy_text") or "",
        headline=slots.get("headline"),
        description=slots.get("description"),
    )


async def _handle_accept_risk(
    db: AsyncSession, current_user: User, session: CampaignChatSession, messages: List[Dict[str, Any]]
) -> None:
    if not await has_role(db, current_user, "OPERATOR"):
        messages.append(cards.build_error_message(
            "FORBIDDEN", "Esta acción requiere el rol OPERATOR o superior en la organización."
        ))
        return

    if session.draft_id:
        # Idempotencia: ya se creó (doble clic o reintento de red). No se
        # vuelve a llamar a start_campaign_creation.
        messages.append(cards.build_progress_message(session.draft_id))
        session.state = "CREATING"
        return

    ad_account = await db.get(AdAccount, session.ad_account_id)
    if await cap_reached(db, current_user.organization_id, ad_account):
        org = await db.get(Organization, current_user.organization_id)
        rules = get_campaign_rules_for_org(org)
        limit = rules.get("max_campaigns_per_window", 15)
        window_hours = rules.get("campaign_window_hours", 24)
        messages.append(cards.build_error_message(
            "CAP_REACHED", cards.cap_reached_text(limit, window_hours), retry_after_hours=window_hours,
        ))
        return

    request = _build_create_request(session)
    try:
        result = await start_campaign_creation(db, current_user, ad_account, request)
    except CampaignCapReachedError as e:
        messages.append(cards.build_error_message(
            "CAP_REACHED", cards.cap_reached_text(e.limit, e.window_hours), retry_after_hours=e.window_hours,
        ))
        return
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else str(e.detail)
        messages.append(cards.build_error_message("PREFLIGHT_FAILED", f"{cards.PREFLIGHT_FAILED_TEXT} {detail}"))
        return

    session.draft_id = result["draft_id"]
    session.state = "CREATING"
    messages.append(cards.build_progress_message(result["draft_id"]))


async def _handle_accept(
    db: AsyncSession, current_user: User, session: CampaignChatSession, messages: List[Dict[str, Any]]
) -> None:
    if session.state == "REVIEW_PLAN":
        await _handle_accept_review(db, current_user, session, messages)
    elif session.state == "CONFIRM_RISK":
        await _handle_accept_risk(db, current_user, session, messages)
    else:
        messages.append(cards.build_error_message("VALIDATION", "No hay nada que confirmar en este momento."))


def _handle_reject(session: CampaignChatSession, messages: List[Dict[str, Any]]) -> None:
    text = cards.REJECTED_AT_REVIEW_TEXT if session.state == "REVIEW_PLAN" else cards.REJECTED_AT_RISK_TEXT
    _close_as_cancelled(session, messages, text)


# --- Extracción (determinista primero, LLM solo si hace falta) ---

async def _try_llm_extraction(
    db: AsyncSession, current_user: User, session: CampaignChatSession, text: str
) -> Optional[extraction.ExtractionResult]:
    if (session.llm_calls or 0) >= _max_understanding_calls(session):
        return None

    org = await db.get(Organization, current_user.organization_id)
    client = OpenRouterClient(organization=org)
    usage_ctx = UsageContext(
        feature="campaign_chat", organization_id=current_user.organization_id, ad_account_id=session.ad_account_id,
    )
    try:
        raw = await client.extract_campaign_slots(org, text, session.state, session.slots_json or {}, usage_ctx=usage_ctx)
    except Exception:
        logger.exception("Fallo inesperado extrayendo slots del chat (sesión %s)", session.id)
        raw = None
    session.llm_calls = (session.llm_calls or 0) + 1

    if raw is None:
        return None

    slots = {k: v for k, v in raw.items() if k in _APPLICABLE_EXTRACTED_KEYS and v is not None}
    slots = extraction.strip_numeric_slots_without_digit_evidence(text, slots)
    if "product_name" in slots:
        slots["product_name"] = sanitize_product_name(slots["product_name"])

    intent = raw.get("intent") or "unclear"
    return extraction.ExtractionResult(intent=intent, slots=slots)


def _ultimo_intercambio(messages: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """Última (pregunta del cliente, respuesta dada) de la conversación.

    Es lo que permite resolver una repregunta. Sin esto, "¿cómo que 0 por
    contacto?" llega sin referente y el chat responde "eso se me escapa" a
    alguien que pregunta por la respuesta que él mismo acaba de dar.
    """
    pregunta = respuesta = None
    for msg in reversed(messages):
        if msg.get("type") != "text":
            continue
        texto = (msg.get("text") or "").strip()
        if not texto:
            continue
        if respuesta is None and msg.get("role") == "assistant":
            respuesta = texto
        elif respuesta is not None and msg.get("role") == "user":
            pregunta = texto
            break
    return (pregunta, respuesta) if pregunta and respuesta else None


async def _try_llm_plan(
    db: AsyncSession, current_user: User, session: CampaignChatSession, text: str,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[ChatPlan], Optional[Period]]:
    """Segunda oportunidad para una pregunta que los regex no reconocieron (H3).

    Le pide al LLM un plan analítico abierto (bloques + períodos) llamando a
    `plan_chat_request`. Preserva AI-1: cero cifras en la respuesta del modelo,
    solo etiquetas de bloques.
    """
    if (session.llm_calls or 0) >= _max_understanding_calls(session):
        return None, None

    org = await db.get(Organization, current_user.organization_id)
    usage_ctx = UsageContext(
        feature="campaign_chat", organization_id=current_user.organization_id,
        ad_account_id=session.ad_account_id,
    )
    try:
        plan = await OpenRouterClient(organization=org).plan_chat_request(
            org, text, usage_ctx=usage_ctx,
            previous_exchange=_ultimo_intercambio(messages or []),
        )
    except Exception:
        logger.exception("Fallo planificando la pregunta del chat con LLM (sesión %s)", session.id)
        return None, None
    session.llm_calls = (session.llm_calls or 0) + 1
    if not plan or not plan.blocks:
        return None, None

    # El período determinista del texto manda sobre el del LLM
    period = parse_period(text)
    if not period and plan.period_key:
        period = period_from_enum(plan.period_key) or Period(
            int(plan.period_key.replace("d", "")) if plan.period_key.endswith("d") and plan.period_key[:-1].isdigit() else 30,
            f"los últimos {plan.period_key}"
        )
    return plan, period


async def _say(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    text: str, user_text: str,
) -> Dict[str, Any]:
    """Mensaje del asistente, reescrito por el LLM para que suene a persona.

    El texto que entra ya viene armado por el código, con las cifras adentro:
    el modelo cambia el tono, nunca los datos. `rephrase.safe_or_canonical`
    descarta la reescritura si le sobra o le falta un número o un enlace, así
    que en el peor caso el usuario ve el texto de siempre — nunca una cifra
    que el modelo se haya inventado (AI-1).
    """
    if (session.turn_count or 0) >= _max_analysis_turns(session):
        return cards.text_message(text)

    org = await db.get(Organization, current_user.organization_id)
    usage_ctx = UsageContext(
        feature="campaign_chat", organization_id=current_user.organization_id,
        ad_account_id=session.ad_account_id,
    )
    try:
        rewritten = await OpenRouterClient(organization=org).rephrase_chat_message(
            org, text, user_text, usage_ctx=usage_ctx,
        )
    except Exception:
        logger.exception("Fallo reescribiendo un mensaje del chat (sesión %s)", session.id)
        rewritten = None

    if rewritten is None:
        return cards.text_message(text)
    return cards.text_message(rephrase.safe_or_canonical(text, rewritten))


async def _analyze(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    user_text: str, topic: str, canonical: str, facts: Dict[str, Any],
) -> Dict[str, Any]:
    """Respuesta sobre el historial, redactada por el LLM desde las cifras
    reales (CH-8).

    Acá el modelo tiene margen para analizar de verdad —elegir qué destacar,
    comparar, explicar qué significa— y no solo maquillar una plantilla. Lo que
    lo hace seguro no es el prompt sino `numbers_are_grounded`: cada número que
    escriba tiene que existir en `facts`, así que no puede inventar ni calcular
    una cifra sobre la plata del cliente. Si mete uno nuevo, se descarta su
    respuesta entera y se usa la del código.

    Es deliberadamente MÁS libre que `_say`, que se sigue usando para el flujo
    de creación: ahí cada palabra empuja a una acción irreversible y la
    exigencia es igualdad exacta.
    """
    if (session.turn_count or 0) >= _max_analysis_turns(session):
        return cards.text_message(canonical)

    org = await db.get(Organization, current_user.organization_id)
    usage_ctx = UsageContext(
        feature="campaign_chat", organization_id=current_user.organization_id,
        ad_account_id=session.ad_account_id,
    )
    facts_blob = json.dumps(facts, ensure_ascii=False, default=str)
    try:
        written = await OpenRouterClient(organization=org).compose_chat_answer(
            org, user_text, topic, facts, usage_ctx=usage_ctx,
        )
    except Exception:
        logger.exception("Fallo analizando el historial (sesión %s, tema %s)", session.id, topic)
        written = None

    if written is None:
        return cards.text_message(canonical)
    return cards.text_message(rephrase.grounded_or_canonical(facts_blob, canonical, written))


async def _handle_off_topic(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    text: str, messages: List[Dict[str, Any]],
) -> None:
    """Pregunta sin tema reconocido por regex: se le pide un plan analítico abierto al LLM (H3)."""
    plan, period = await _try_llm_plan(db, current_user, session, text, messages)
    if plan and plan.blocks:
        await _answer_and_resume(
            db, current_user, session, plan, messages, user_text=text, period=period,
        )
        return
    messages.append(await _say(db, current_user, session, questions.OUT_OF_SCOPE_TEXT, text))
    pendiente = _pending_prompt_for_state(session.state)
    if pendiente:
        messages.append(cards.text_message(pendiente))


def _slots_from_result(result: extraction.ExtractionResult, *, is_gate: bool) -> Dict[str, Any]:
    slots = dict(result.slots)
    bare = slots.pop("bare_number", None)
    if bare is not None and is_gate:
        slots["budget"] = bare
    return slots


async def _answer_and_resume(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    topic_or_plan: Any, messages: List[Dict[str, Any]], user_text: str = "",
    period: Optional[Period] = None,
) -> None:
    """Responde una pregunta sobre la marca SIN mover el estado ni tocar los
    slots, y vuelve a dejar planteado lo que estaba pendiente — preguntar algo
    a mitad del armado no debe hacerte perder el lugar (CH-6)."""
    ad_account = await db.get(AdAccount, session.ad_account_id)
    facts = None
    if isinstance(topic_or_plan, list):
        topic = " / ".join(topic_or_plan)
    else:
        topic = getattr(topic_or_plan, "topic", topic_or_plan) or "SUMMARY"
    try:
        facts, answer = await facts_module.resolve_facts(
            db, current_user.organization_id, ad_account, topic_or_plan, session.slots_json or {},
            window_days=period.days if period else None,
        )
    except Exception:
        logger.exception("No se pudo responder la pregunta '%s' (sesión %s)", topic, session.id)
        answer = questions.NO_DATA_TEXT
        facts = None

    if topic in ("HOW_IT_WORKS", "WHY_PAUSED") or not facts:
        messages.append(await _say(db, current_user, session, answer, user_text))
    else:
        # Preguntas sobre el pasado: el LLM redacta el análisis desde las cifras (CH-8).
        messages.append(await _analyze(db, current_user, session, user_text, topic, answer, facts))

    pendiente = _pending_prompt_for_state(session.state)
    if pendiente:
        messages.append(cards.text_message(pendiente))


def _pending_prompt_for_state(state: str) -> Optional[str]:
    if state == "ASK_GOAL":
        return cards.ASK_GOAL_TEXT
    if state == "ASK_MEDIA":
        return cards.ASK_MEDIA_TEXT
    if state in GATE_STATES:
        return cards.RESUME_GATE_TEXT
    return None


async def _handle_text(
    db: AsyncSession, current_user: User, session: CampaignChatSession, text: str, messages: List[Dict[str, Any]]
) -> None:
    is_gate = session.state in GATE_STATES
    result = extraction.extract_message(text, state=session.state, is_gate=is_gate)

    slots = dict(session.slots_json or {})
    if slots.get("_pending_escalation"):
        t_clean = text.lower()
        if result.intent == "accept" or any(w in t_clean for w in ("si", "sí", "dale", "armar", "crear", "adelante", "armemos")):
            if not await has_role(db, current_user, "OPERATOR"):
                messages.append(cards.text_message("Para armar y publicar campañas necesitás el rol de Operador o Administrador en la organización."))
                slots.pop("_pending_escalation", None)
                session.slots_json = slots
                return
            ad_acc = await db.get(AdAccount, session.ad_account_id)
            page, err = await resolve_destination(db, ad_acc, current_user.organization_id)
            if page is None:
                messages.append(cards.text_message(err or cards.GUARD_NO_PAGE_TEXT))
                slots.pop("_pending_escalation", None)
                session.slots_json = slots
                return
            session.mode = "BUILD"
            slots.pop("_pending_escalation", None)
            slots["page_id"] = page.external_page_id
            slots["whatsapp_number"] = page.whatsapp_number
            session.slots_json = slots
            session.state = next_state(_facts(session))
            messages.append(cards.text_message("¡Excelente! Vamos a armar la campaña."))
            pendiente = _pending_prompt_for_state(session.state)
            if pendiente:
                messages.append(cards.text_message(pendiente))
            return
        else:
            slots.pop("_pending_escalation", None)
            session.slots_json = slots
            messages.append(cards.text_message("Entendido, seguimos consultando métricas. ¿Qué otra duda tenés?"))
            return

    # Una pregunta reconocida por regex se responde con datos reales sin gastar
    # un token (CH-6). Si ningún regex la reconoce, recién ahí entra el LLM a
    # elegir el tema — antes se rechazaba de una, y eso descartaba preguntas
    # que el sistema sabía responder con datos que ya tenía en la base.
    if result.intent == "question":
        topics = result.question_topics or ([result.question_topic] if result.question_topic else [])
        if len(topics) >= 2:
            # Pregunta compuesta con múltiples temas detectados por regex: 0 tokens
            await _answer_and_resume(
                db, current_user, session, topics, messages,
                user_text=text, period=result.question_period,
            )
            return
        elif len(topics) == 1:
            # Si hay coordinación explícita (e.g. "y", "además"), evaluamos si el LLM
            # puede planificar una segunda intención abierta no cubierta por regex
            if _COORDINATION_RE.search(strip_accents(text)):
                plan, period = await _try_llm_plan(db, current_user, session, text, messages)
                if plan and plan.blocks and len(plan.blocks) > 1:
                    await _answer_and_resume(
                        db, current_user, session, plan, messages,
                        user_text=text, period=period or result.question_period,
                    )
                    return
            await _answer_and_resume(
                db, current_user, session, topics[0], messages,
                user_text=text, period=result.question_period,
            )
            return
    if result.intent == "off_topic":
        await _handle_off_topic(db, current_user, session, text, messages)
        return

    if session.mode == "ASK" and (session.state == "BROWSING"):
        if result.intent in ("provide_info", "change") and not result.question_topic:
            slots["_pending_escalation"] = True
            if result.goal_text:
                slots["goal_text"] = result.goal_text
            session.slots_json = slots
            messages.append(cards.text_message(
                "¿Querés que armemos una campaña para promocionar esto?",
                quick_replies=["Sí, armemos la campaña", "No, solo estoy consultando"]
            ))
            return

    if result.intent == "unclear":
        llm_result = await _try_llm_extraction(db, current_user, session, text)
        if llm_result is not None:
            result = llm_result

    if result.intent == "cancel":
        _close_as_cancelled(session, messages, cards.CANCELLED_TEXT)
        return

    if is_gate and result.intent == "accept":
        await _handle_accept(db, current_user, session, messages)
        return
    if is_gate and result.intent == "reject":
        _handle_reject(session, messages)
        return

    if result.intent in ("change", "provide_info"):
        changes = _slots_from_result(result, is_gate=is_gate)
        _apply_slot_changes(session, changes)
        await _run_auto_steps(db, current_user, session, messages, user_text=text)
        return

    messages.append(await _say(db, current_user, session, _unclear_text_for_state(session.state), text))


def _validate_media_ownership(current_user: User, image_path: Optional[str], video_path: Optional[str]) -> None:
    prefix = f"{current_user.organization_id}/"
    for path in (image_path, video_path):
        if path and not path.startswith(prefix):
            raise HTTPException(status_code=400, detail="El archivo no pertenece a esta organización.")


def _ensure_open(session: CampaignChatSession) -> None:
    if session.status != "OPEN":
        raise HTTPException(status_code=409, detail="Esta conversación ya terminó.")


# --- Entradas públicas: mensaje de texto/media y decisión sobre una tarjeta ---

async def process_message(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    client_turn_id: str, text: Optional[str], image_path: Optional[str], video_path: Optional[str],
) -> CampaignChatSession:
    _ensure_open(session)
    if session.last_client_turn_id == client_turn_id:
        return session

    messages = list(session.messages_json or [])

    if image_path or video_path:
        _validate_media_ownership(current_user, image_path, video_path)
        messages.append(cards.media_message(image_path, video_path))
        changes: Dict[str, Any] = {}
        if image_path:
            changes["image_path"] = image_path
            changes["video_path"] = None
        else:
            changes["video_path"] = video_path
            changes["image_path"] = None
        _apply_slot_changes(session, changes)
        await _run_auto_steps(db, current_user, session, messages)
    elif text:
        messages.append(cards.user_text_message(text))
        await _handle_text(db, current_user, session, text, messages)

    session.turn_count = (session.turn_count or 0) + 1
    session.last_client_turn_id = client_turn_id
    session.messages_json = messages[-MAX_MESSAGES_PERSISTED:]
    await db.commit()
    await db.refresh(session)
    return session


async def process_decision(
    db: AsyncSession, current_user: User, session: CampaignChatSession,
    client_turn_id: str, card_id: str, decision: str, text: Optional[str],
) -> CampaignChatSession:
    _ensure_open(session)
    if session.last_client_turn_id == client_turn_id:
        return session

    messages = list(session.messages_json or [])
    label = {"accept": "Aceptar", "reject": "Rechazar", "change": text or "Cambiar"}[decision]
    messages.append(cards.user_text_message(label))

    if decision == "reject":
        _handle_reject(session, messages)
    elif decision == "accept":
        await _handle_accept(db, current_user, session, messages)
    elif decision == "change":
        is_gate = session.state in GATE_STATES
        result = extraction.extract_message(text or "", state=session.state, is_gate=True)
        if result.intent == "question":
            topics = result.question_topics or ([result.question_topic] if result.question_topic else [])
            if topics:
                await _answer_and_resume(
                    db, current_user, session, topics if len(topics) > 1 else topics[0], messages,
                    user_text=text or "", period=result.question_period,
                )
        elif result.intent == "off_topic":
            await _handle_off_topic(db, current_user, session, text or "", messages)
        else:
            if result.intent == "unclear":
                llm_result = await _try_llm_extraction(db, current_user, session, text or "")
                if llm_result is not None:
                    result = llm_result
            changes = _slots_from_result(result, is_gate=is_gate) if result.intent in ("change", "provide_info") else {}
            _apply_slot_changes(session, changes)
            await _run_auto_steps(db, current_user, session, messages, user_text=text or "")

    session.turn_count = (session.turn_count or 0) + 1
    session.last_client_turn_id = client_turn_id
    session.messages_json = messages[-MAX_MESSAGES_PERSISTED:]
    await db.commit()
    await db.refresh(session)
    return session
