"""Definición y normalización de los slots que llena el chat de creación de
campañas. Puro (sin BD, sin HTTP) — la validación autoritativa sigue viviendo
en `CampaignValidator.validate` dentro de `start_campaign_creation`; esto solo
existe para poder corregir al usuario EN EL MOMENTO ("le saqué las tildes al
nombre") en vez de fallar recién al confirmar la creación (CH-1).
"""
import re
import unicodedata
from typing import Optional

from app.services.campaign_validator import PRODUCT_NAME_PATTERN

MAX_PRODUCT_NAME_CHARS = 40

# Palabras de relleno frecuentes en la forma en que un cliente describe su
# objetivo ("quiero vender más zapatillas para hombre") que no aportan nada al
# nombre técnico del producto.
_STOPWORDS = {
    "quiero", "quisiera", "necesito", "vender", "promocionar", "promover",
    "anunciar", "mas", "más", "para", "de", "del", "la", "el", "los", "las",
    "un", "una", "unos", "unas", "mi", "mis", "nuestro", "nuestra", "negocio",
    "producto", "productos", "servicio", "servicios", "empresa", "que",
    "lograr", "conseguir", "aumentar", "generar", "y", "en", "con", "por",
    # Pedidos dirigidos al asistente ("¿me ayudás a vender zapatillas?"):
    # nunca forman parte del nombre del producto.
    "me", "a", "al", "ayuda", "ayudame", "ayudas", "ayudar", "podes", "puedes",
    "quisiera", "hacer", "armar", "crear", "campana", "campanas",
}

_ALLOWED_CHARS_RE = re.compile(r"[^A-Za-z0-9 _-]")
_WHITESPACE_RE = re.compile(r"\s+")

_PRODUCT_NAME_RE = re.compile(PRODUCT_NAME_PATTERN)


def strip_accents(text: str) -> str:
    """Transliteración ASCII (tildes, ñ, emojis fuera). `CampaignValidator`
    exige `^[A-Za-z0-9 _-]+$`; sin esto, cualquier frase en español real
    rompería la creación al final del flujo."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def sanitize_product_name(raw: Optional[str]) -> str:
    """Deriva un `product_name` válido para Meta/`CampaignValidator` a partir
    de una frase libre (`goal_text`) o de lo que haya dicho el usuario. Se usa
    tanto sobre la salida del extractor determinista como sobre la del LLM —
    así solo una función puede producir un `product_name` inválido."""
    if not raw or not raw.strip():
        return "Producto"

    ascii_text = strip_accents(raw)
    cleaned = _ALLOWED_CHARS_RE.sub(" ", ascii_text)
    words = [w for w in _WHITESPACE_RE.split(cleaned) if w]

    significant = [w for w in words if w.lower() not in _STOPWORDS]
    if not significant:
        significant = words

    chosen = significant[:4] if significant else []
    name = " ".join(w.capitalize() for w in chosen).strip()
    name = _WHITESPACE_RE.sub(" ", name)[:MAX_PRODUCT_NAME_CHARS].strip()

    if not name or not _PRODUCT_NAME_RE.match(name):
        return "Producto"
    return name


def normalize_whatsapp(raw: Optional[str]) -> Optional[str]:
    """Solo dígitos, sin '+' ni espacios — lo que exige `CampaignCreateRequest.whatsapp_number`."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits or None


# Slots cuyo cambio invalida el copy/presupuesto ya generado (hay que volver a
# pedirle al LLM/CampaignSuggester, porque cambia de qué está hablando el
# anuncio). Slots que solo invalidan la ACEPTACIÓN (el número cambió, pero
# sigue siendo la misma campaña) están en `SOFT_INVALIDATING_SLOTS`.
HARD_INVALIDATING_SLOTS = {"goal_text", "product_name", "image_path", "video_path"}
SOFT_INVALIDATING_SLOTS = {
    "budget", "budget_type", "days", "page_id", "whatsapp_number",
    "copy_text", "headline", "description",
}
