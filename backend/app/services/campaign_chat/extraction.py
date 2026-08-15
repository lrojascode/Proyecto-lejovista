"""Extractor determinista de intención/slots del chat de creación de campañas
(CH-1). Puro — sin BD, sin HTTP, sin LLM. Es el camino que corre bajo
`MOCK_SERVICES=true` (y por lo tanto en CI): tiene que poder completar la
conversación entera sin ayuda del modelo. El método `extract_campaign_slots`
del LLM (`app/services/ai/llm.py`) se usa solo para mejorar la comprensión de
texto libre ambiguo — nunca para decidir una cifra (regla AI-1): el motor
descarta cualquier número que el LLM proponga si no hay un dígito literal en
el mensaje del usuario.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.services.campaign_chat.period import Period, parse_period
from app.services.campaign_chat.questions import classify_question, looks_like_question
from app.services.campaign_chat.slots import normalize_whatsapp, sanitize_product_name, strip_accents

MAX_GOAL_TEXT_CHARS = 500

# --- Intenciones cruzadas: válidas en cualquier estado no terminal ---
_CANCEL_RE = re.compile(
    r"\b(cancela(?:r)?|olvidalo|dejalo|salir|ya no quiero|mejor cancelamos)\b", re.IGNORECASE
)
# Ancladas al inicio del mensaje: "no se si si" no debe leerse como aceptación,
# y una respuesta larga que arranca con "sí, pero..." sí debe contar como sí.
_ACCEPT_RE = re.compile(
    r"^(s[ií]|sip|dale|ok|okey|vale|listo|correcto|asi esta bien|perfecto|de una"
    r"|va|confirmo|acepto|adelante|de acuerdo|ya)\b",
    re.IGNORECASE,
)
_REJECT_RE = re.compile(
    r"^(no|nop|nel|para|detente|mejor no|rechazo|no me gusta)\b", re.IGNORECASE
)
_CHANGE_HINT_RE = re.compile(
    r"\b(cambia(?:r)?|modifica(?:r)?|edita(?:r)?|mejor|en vez de|quiero que sea|sube(?:le)?|baja(?:le)?|"
    r"aumenta(?:r)?|reduce|corrige(?:r)?)\b",
    re.IGNORECASE,
)

# --- Slots numéricos/estructurados ---
_BUDGET_RE = re.compile(
    r"(?:s/\.?|soles?|pen|\$)\s*([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"|([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?:soles?|s/\.?|pen|dolares?)",
    re.IGNORECASE,
)
_DAYS_RE = re.compile(r"(\d{1,3})\s*dias?", re.IGNORECASE)
_WEEK_RE = re.compile(r"\b(?:una?|1)\s*semanas?\b", re.IGNORECASE)
_TWO_WEEKS_RE = re.compile(r"\b(?:dos|2)\s*semanas?\b", re.IGNORECASE)
_MONTH_RE = re.compile(r"\b(?:un|1)\s*mes\b", re.IGNORECASE)
_DAILY_RE = re.compile(r"\b(diario|por dia|al dia|cada dia)\b", re.IGNORECASE)
_LIFETIME_RE = re.compile(r"\b(total|en total|en toda la campana)\b", re.IGNORECASE)
_WHATSAPP_RE = re.compile(r"(?:\+?51)?\s*9\d{2}[\s-]?\d{3}[\s-]?\d{3}")
_BARE_NUMBER_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
# Verbos con los que alguien describe qué quiere anunciar. Son la señal de que
# una frase con forma de pregunta es en realidad el objetivo de la campaña.
_GOAL_INTENT_RE = re.compile(
    r"\b(vender|promocionar|promover|anunciar|ofrecer|publicitar|dar a conocer|"
    r"promocionarme|difundir)\b",
    re.IGNORECASE,
)
# Cuando el usuario menciona "presupuesto" sin unidad ("sube el presupuesto a
# 400"), el número más cercano es casi siempre eso — sin esto, `parse_budget`
# solo reconoce montos con "soles"/"S/."/"$" explícito.
_BUDGET_CONTEXT_RE = re.compile(r"presupuesto")
_ANY_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _normalize_number(raw: str) -> Optional[float]:
    """Normaliza separadores de miles/decimales en español y en inglés:
    "1.200,50" (es) y "1,200.50" (en) deben dar el mismo número."""
    s = raw.strip()
    if not s:
        return None
    has_comma, has_dot = "," in s, "." in s
    try:
        if has_comma and has_dot:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif has_comma:
            tail = s.split(",")[-1]
            s = s.replace(",", ".") if len(tail) == 2 else s.replace(",", "")
        elif has_dot:
            tail = s.split(".")[-1]
            if len(tail) != 2:
                s = s.replace(".", "")
        return float(s)
    except ValueError:
        return None


def parse_budget(text: str) -> Optional[float]:
    match = _BUDGET_RE.search(text)
    if match:
        raw = match.group(1) or match.group(2)
        value = _normalize_number(raw)
        return value if value and value > 0 else None
    if _BUDGET_CONTEXT_RE.search(text):
        num_match = _ANY_NUMBER_RE.search(text)
        if num_match:
            value = _normalize_number(num_match.group(1))
            return value if value and value > 0 else None
    return None


def parse_days(text: str) -> Optional[int]:
    if _TWO_WEEKS_RE.search(text):
        return 14
    if _WEEK_RE.search(text):
        return 7
    if _MONTH_RE.search(text):
        return 30
    match = _DAYS_RE.search(text)
    if match:
        days = int(match.group(1))
        return days if days > 0 else None
    return None


def parse_budget_type(text: str) -> Optional[str]:
    if _DAILY_RE.search(text):
        return "DAILY"
    if _LIFETIME_RE.search(text):
        return "LIFETIME"
    return None


def parse_whatsapp(text: str) -> Optional[str]:
    match = _WHATSAPP_RE.search(text)
    return normalize_whatsapp(match.group(0)) if match else None


def _parse_numeric_slots(ascii_text: str) -> Dict[str, Any]:
    """Solo devuelve un slot numérico si hay al menos un dígito literal en el
    texto del usuario — es la garantía dura de que ni este extractor ni (más
    importante) el del LLM pueden "inventar" una cifra (regla AI-1)."""
    if not re.search(r"\d", ascii_text):
        return {}
    found: Dict[str, Any] = {}
    budget = parse_budget(ascii_text)
    if budget is not None:
        found["budget"] = budget
    days = parse_days(ascii_text)
    if days is not None:
        found["days"] = days
    whatsapp = parse_whatsapp(ascii_text)
    if whatsapp is not None:
        found["whatsapp_number"] = whatsapp
    return found


_NUMERIC_SLOT_KEYS = {"budget", "days"}


def strip_numeric_slots_without_digit_evidence(text: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """Descarta `budget`/`days` de un dict de slots (típicamente la salida del
    LLM) si el mensaje original del usuario no contiene ningún dígito. Es la
    garantía dura de la regla AI-1 en el chat: el LLM puede entender que el
    usuario "quiere subir el presupuesto", pero nunca puede ser la fuente de
    LA CIFRA — si no hay un dígito literal en lo que la persona escribió, el
    número no se usa, sin importar qué haya devuelto el modelo."""
    if re.search(r"\d", text):
        return dict(slots)
    return {k: v for k, v in slots.items() if k not in _NUMERIC_SLOT_KEYS}


@dataclass
class ExtractionResult:
    intent: str  # accept | reject | cancel | provide_info | change | question | off_topic | unclear
    slots: Dict[str, Any] = field(default_factory=dict)
    # Solo con intent="question": el tema soportado que resolvió `questions.py`.
    question_topic: Optional[str] = None
    # Período mencionado en la pregunta ("este mes", "los últimos 30 días").
    # `None` = no mencionó ninguno y se usa la ventana por defecto.
    question_period: Optional[Period] = None


def extract_message(text: str, *, state: str, is_gate: bool) -> ExtractionResult:
    """Único punto de entrada determinista. `state` es el estado actual de la
    sesión; `is_gate` indica si hay una tarjeta de revisión/riesgo pendiente
    (en ese caso, "sí"/"no" sueltos deciden la tarjeta en vez de rellenar un
    slot)."""
    raw = (text or "").strip()
    if not raw:
        return ExtractionResult(intent="unclear")

    ascii_text = strip_accents(raw).lower()

    if _CANCEL_RE.search(ascii_text):
        return ExtractionResult(intent="cancel")

    if is_gate:
        if _ACCEPT_RE.match(ascii_text):
            return ExtractionResult(intent="accept")
        if _REJECT_RE.match(ascii_text):
            return ExtractionResult(intent="reject")

    # Preguntas sobre la marca (CH-6): se resuelven antes que cualquier slot
    # para que "¿cuánto gasté?" no termine interpretado como un presupuesto ni
    # como el objetivo de la campaña. Un tema reconocido acá se responde con
    # datos reales sin gastar un token; `off_topic` NO es la palabra final —
    # el motor le da una segunda pasada al LLM antes de rechazar.
    topic = classify_question(raw)
    if topic:
        return ExtractionResult(intent="question", question_topic=topic, question_period=parse_period(raw))
    if looks_like_question(raw):
        # Una pregunta que no es de la marca se rechaza... salvo que sea la
        # forma cortés de decir el objetivo ("¿me ayudás a vender zapatillas?"),
        # que solo tiene sentido cuando justo se preguntó qué promocionar. El
        # verbo comercial es lo que las separa: sin él, "¿cuánto está el dólar
        # hoy?" terminaba guardado como nombre del producto.
        es_objetivo_en_pregunta = state == "ASK_GOAL" and bool(_GOAL_INTENT_RE.search(ascii_text))
        if not es_objetivo_en_pregunta:
            return ExtractionResult(intent="off_topic")

    numeric_slots = _parse_numeric_slots(ascii_text)
    budget_type = parse_budget_type(ascii_text)
    if budget_type:
        numeric_slots["budget_type"] = budget_type

    if state == "ASK_GOAL" and not is_gate:
        # Casi cualquier frase es una respuesta válida a "¿qué querés
        # promocionar?" — se toma el texto entero como objetivo, incluso si
        # de paso menciona un número (ese número no se descarta: alimenta
        # también budget/days si el usuario ya lo dio de una).
        slots: Dict[str, Any] = {
            "goal_text": raw[:MAX_GOAL_TEXT_CHARS],
            "product_name": sanitize_product_name(raw),
        }
        slots.update(numeric_slots)
        return ExtractionResult(intent="provide_info", slots=slots)

    if numeric_slots:
        intent = "change" if (is_gate or _CHANGE_HINT_RE.search(ascii_text)) else "provide_info"
        return ExtractionResult(intent=intent, slots=numeric_slots)

    if _BARE_NUMBER_RE.match(ascii_text):
        # Un número suelto respondiendo a la pregunta actual (sin unidad):
        # se interpreta según lo que se le esté pidiendo al usuario. El motor
        # decide a qué slot va — acá solo se marca que hay un número crudo.
        value = _normalize_number(ascii_text)
        if value is not None:
            return ExtractionResult(intent="provide_info", slots={"bare_number": value})

    return ExtractionResult(intent="unclear")
