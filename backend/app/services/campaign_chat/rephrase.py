"""Reescritura conversacional de las respuestas del chat (CH-7). Puro — sin
BD, sin HTTP.

El código sigue siendo dueño de QUÉ se dice: arma el texto canónico con las
cifras reales de la cuenta. El LLM solo reescribe CÓMO se dice, para que la
conversación no suene a formulario con globos de diálogo.

Y no se le cree al modelo: antes de usar una reescritura se verifica que
tenga exactamente los mismos números que el original y los mismos enlaces. Si
algo no cuadra, se descarta y se usa el texto canónico. Esa verificación es lo
que mantiene la regla AI-1 en pie acá: el LLM no puede inventar una cifra
porque cualquier cifra nueva —o cualquier cifra que falte— invalida su salida
entera, sin importar qué tan convincente suene.
"""
import re
from typing import Set

# Cotas de forma: una reescritura razonable no puede ser mucho más larga que el
# original (señal de que el modelo se puso a agregar cosas por su cuenta) ni
# quedar en dos palabras (señal de que se comió la mitad de la respuesta).
MAX_GROWTH_RATIO = 1.8
MIN_SHRINK_RATIO = 0.4
MAX_REPHRASE_CHARS = 1500

# El piso de longitud solo se le exige a los textos largos: una respuesta con
# datos que queda en un tercio perdió información, pero una pregunta corta
# ("¿Qué querés promocionar? Contámelo con tus palabras.") se puede acortar a
# "¿Qué querés promocionar?" sin perder nada — y esa es justamente la clase de
# reescritura que hace que el chat suene a persona.
MIN_LENGTH_FOR_SHRINK_CHECK = 120

_NUMBER_RE = re.compile(r"\d[\d.,]*")
_URL_RE = re.compile(r"https?://\S+")


def _normalize_number(token: str) -> str:
    """Reduce un número a sus dígitos para comparar sin que el formato importe:
    "1.085,47", "1,085.47" y "1085.47" son el mismo número. Un separador final
    colgado ("15." al terminar la frase) no cuenta."""
    return token.replace(".", "").replace(",", "")


def numeric_tokens(text: str) -> Set[str]:
    return {_normalize_number(m.group(0)) for m in _NUMBER_RE.finditer(text or "")}


def url_tokens(text: str) -> Set[str]:
    return set(_URL_RE.findall(text or ""))


def is_safe_rephrase(original: str, rewritten: str) -> bool:
    """¿Se puede usar esta reescritura en lugar del texto canónico?

    Se exige IGUALDAD de números, no subconjunto: un número de más sería una
    cifra inventada, y uno de menos sería una respuesta que perdió el dato por
    el que preguntaron. Ante cualquier duda, `False` — el costo de equivocarse
    es mostrarle al cliente una cifra falsa sobre su propia plata.
    """
    rewritten = (rewritten or "").strip()
    if not rewritten or len(rewritten) > MAX_REPHRASE_CHARS:
        return False

    original = original or ""
    if len(rewritten) > len(original) * MAX_GROWTH_RATIO:
        return False
    if len(original) >= MIN_LENGTH_FOR_SHRINK_CHECK and len(rewritten) < len(original) * MIN_SHRINK_RATIO:
        return False

    if numeric_tokens(rewritten) != numeric_tokens(original):
        return False
    if url_tokens(rewritten) != url_tokens(original):
        return False
    return True


def safe_or_canonical(original: str, rewritten: str) -> str:
    """Punto de entrada del motor: devuelve la reescritura si pasa el control,
    o el texto canónico intacto si no."""
    return rewritten.strip() if is_safe_rephrase(original, rewritten) else original


# --- Análisis del historial: el LLM redacta, pero no calcula (CH-8) ---
from app.services.campaign_chat.grounding import numbers_are_grounded, grounded_or_canonical
