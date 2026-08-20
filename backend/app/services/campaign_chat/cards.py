"""Textos y tarjetas del chat de creación de campañas (CH-1). Los títulos,
etiquetas y preguntas son plantillas ESTÁTICAS en español — no generación: la
misma pregunta debe salir igual siempre. Lo único que puede variar por turno
es el texto que el usuario escribió y (más adelante) un `reply_hint` corto del
LLM para la reconocimiento de la respuesta — nunca las cifras ni las
etiquetas de las tarjetas.
"""
import datetime
import uuid
from typing import Any, Dict, List, Optional

from app.services.campaign_chat.markdown_utils import normalize_markdown

GREETING_TEXT = (
    "¡Hola! Te ayudo a armar una campaña de anuncios en un par de minutos, y también "
    "te respondo cómo van las que ya tenés (gasto, leads, cuál rinde mejor).\n\n"
    "Para empezar: ¿qué querés promocionar? Contámelo como se lo dirías a un amigo."
)
ASK_GOAL_TEXT = "¿Qué querés promocionar? Contámelo con tus palabras."
# Se muestra después de responder una pregunta hecha a mitad de una compuerta,
# para que el usuario no pierda el hilo de lo que estaba por decidir.
RESUME_GATE_TEXT = "Volvamos a lo nuestro: revisá la tarjeta de arriba y decime si le damos. 👆"
ASK_MEDIA_TEXT = "Perfecto. Ahora súbeme la foto del producto o del anuncio 📸"
BUILDING_TEXT = "Dame un momento, estoy armando tu campaña con los datos de tu cuenta..."
CANCELLED_TEXT = "Listo, no se creó nada. Si querés, podés empezar una campaña nueva cuando quieras."
REJECTED_AT_REVIEW_TEXT = "Sin problema. No se creó nada. ¿Querés que ajuste algo o preferís cancelar?"
REJECTED_AT_RISK_TEXT = "Entendido, no se creó la campaña. Podés pedirme cambios o cancelar."
UNCLEAR_TEXT = "No te entendí bien. ¿Podés responder la pregunta de arriba?"
CREATING_TEXT = (
    "¡Vamos! Esto tarda unos 3 minutos porque cada paso espera un poco para no generar "
    "alertas en tu cuenta de Meta. Podés cerrar esta ventana, la campaña te va a estar esperando."
)
def cap_reached_text(limit: int = 15, window_hours: int = 24) -> str:
    return (
        f"Ya llegaste al máximo de {limit} campañas nuevas permitidas en las últimas {window_hours} horas "
        "(protección para que Meta no bloquee tu cuenta por crear demasiadas seguidas)."
    )

CAP_REACHED_TEXT = cap_reached_text(15, 24)
PREFLIGHT_FAILED_TEXT = "Meta encontró un problema al validar estos datos antes de crear la campaña:"
SAGA_FAILED_TEXT = "Algo falló al crear la campaña en Meta:"
GUARD_NO_PAGE_TEXT = (
    "Todavía no tenés una página de Facebook con WhatsApp conectada. "
    "Conectá una en Configuración → Páginas y WhatsApp antes de crear una campaña."
)


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat()


def _base(role: str) -> Dict[str, Any]:
    return {"id": new_id(), "role": role, "created_at": now_iso()}


def text_message(text: str, role: str = "assistant", quick_replies: Optional[List[str]] = None) -> Dict[str, Any]:
    # Solo el texto del asistente pasa por el normalizador: es el único que se
    # renderiza como markdown generado (canónico o reescrito por el LLM). El
    # texto del usuario se muestra tal cual lo escribió.
    if role == "assistant":
        text = normalize_markdown(text)
    msg = {**_base(role), "type": "text", "text": text}
    if quick_replies:
        msg["quick_replies"] = quick_replies
    return msg


def user_text_message(text: str) -> Dict[str, Any]:
    return text_message(text, role="user")


def media_message(image_path: Optional[str] = None, video_path: Optional[str] = None) -> Dict[str, Any]:
    return {**_base("user"), "type": "media", "image_path": image_path, "video_path": video_path}


_SOURCE_LABELS = {
    "ai_suggestion": "sugerido por la IA",
    "user": "tu respuesta",
    "account_default": "de tu cuenta",
    "fixed_rule": "regla fija",
}


def _field(key: str, label: str, value: str, source: str, editable: bool = True) -> Dict[str, Any]:
    return {"key": key, "label": label, "value": value, "source": source, "editable": editable}


def build_review_card(slots: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    budget_type_label = "por día" if slots.get("budget_type") == "DAILY" else "total"
    fields = [
        _field("product_name", "Producto", slots.get("product_name") or "—", "user", editable=False),
        _field(
            "budget", "Presupuesto",
            f"S/. {slots.get('budget', 0):.2f} ({budget_type_label})",
            "user" if slots.get("_budget_from_user") else "ai_suggestion",
        ),
        _field("days", "Duración", f"{slots.get('days', 0)} días", "user" if slots.get("_days_from_user") else "ai_suggestion"),
        _field(
            "campaign_name", "Nombre de la campaña",
            proposal.get("suggested_campaign_name") or "—", "ai_suggestion", editable=False,
        ),
    ]
    copy = proposal.get("copy") or {}
    return {
        **_base("assistant"),
        "type": "review_card",
        "card_id": new_id(),
        "title": "Esta es tu campaña, ¿está bien?",
        "fields": fields,
        "copy_preview": copy,
        "media_preview": {"image_path": slots.get("image_path"), "video_path": slots.get("video_path")},
        "evidence": proposal.get("evidence") or [],
        "actions": [
            {"action": "accept", "label": "Aceptar", "style": "primary"},
            {"action": "change", "label": "Cambiar algo", "style": "neutral"},
            {"action": "reject", "label": "Cancelar", "style": "danger"},
        ],
    }


_RISK_LABELS = {"LOW": "BAJO", "MEDIUM": "MEDIO", "HIGH": "ALTO"}


def build_risk_card(risk: Dict[str, Any], preflight: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    level = risk.get("level", "MEDIUM")
    return {
        **_base("assistant"),
        "type": "risk_card",
        "card_id": new_id(),
        "title": f"Riesgo {_RISK_LABELS.get(level, level)} — ¿la creamos?",
        "risk_level": level,
        "risk_reasons": risk.get("reasons") or [],
        "preflight_ok": (preflight or {}).get("ok"),
        "preflight_checks": (preflight or {}).get("checks") or [],
        "actions": [
            {"action": "accept", "label": "Entiendo el riesgo — Crear (quedará PAUSADA)", "style": "primary"},
            {"action": "reject", "label": "Mejor no", "style": "danger"},
        ],
    }


def build_progress_message(draft_id: str) -> Dict[str, Any]:
    return {
        **_base("assistant"),
        "type": "progress",
        "draft_id": draft_id,
        "step": "creating",
        "percent": 10,
        "text": CREATING_TEXT,
    }


def simulated_result_text(reason: Optional[str]) -> str:
    """El motivo exacto —qué variable y con qué valor— en vez de un "está
    apagado" genérico: hay tres causas posibles y desde la pantalla eran
    indistinguibles."""
    detalle = f"\n\nMotivo: `{reason}`" if reason else ""
    return (
        "⚠️ Simulación: la campaña quedó registrada acá, pero **no se creó en Meta**, "
        "así que no la vas a encontrar en Ads Manager. Todo lo demás —presupuesto, "
        "copy, segmentación— es real y queda guardado." + detalle
    )


def build_result_message(draft_id: str, result: Dict[str, Any], campaign_name: Optional[str]) -> Dict[str, Any]:
    # `simulated` lo escribe la Saga al terminar. Decir "¡Listo!" cuando no se
    # creó nada en Meta es la peor forma de fallar: el usuario se entera recién
    # al no encontrarla en Ads Manager, y sin ninguna pista de por qué.
    simulated = bool(result.get("simulated"))
    return {
        **_base("assistant"),
        "type": "result",
        "draft_id": draft_id,
        "campaign_id": result.get("campaign_id"),
        "adset_id": result.get("adset_id"),
        "ad_id": result.get("ad_id"),
        "campaign_name": campaign_name,
        "simulated": simulated,
        "text": simulated_result_text(result.get("simulated_reason")) if simulated else (
            "¡Listo! Tu campaña quedó creada, pero en PAUSA a propósito — "
            "revisala en Ads Manager y actívala vos cuando quieras."
        ),
    }


def build_error_message(
    code: str, text: str, retry_after_hours: Optional[int] = None, actions: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    msg = {**_base("assistant"), "type": "error", "code": code, "text": text}
    if retry_after_hours is not None:
        msg["retry_after_hours"] = retry_after_hours
    if actions:
        msg["actions"] = actions
    return msg
