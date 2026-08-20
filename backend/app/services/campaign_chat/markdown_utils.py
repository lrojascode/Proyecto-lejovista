"""Normalización de markdown para las respuestas del chat.

`react-markdown` (CommonMark) trata una línea que sigue INMEDIATAMENTE a un
ítem de lista, sin línea en blanco de por medio, como "continuación perezosa"
del mismo párrafo — la pega visualmente al ítem en vez de mostrarla como texto
aparte. El modelo (o el texto canónico) a veces cierra una lista sin esa línea
en blanco; esta función la restaura antes de guardar el mensaje.
"""
import re
from typing import List

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def normalize_markdown(text: str) -> str:
    """Garantiza una línea en blanco entre el final de una lista y el párrafo
    siguiente. Pura, idempotente: no toca nada más del texto."""
    if not text or "\n" not in text:
        return text

    lines = text.split("\n")
    out: List[str] = []
    prev_was_list_item = False
    for line in lines:
        is_list_item = bool(_LIST_ITEM_RE.match(line))
        is_blank = not line.strip()
        if prev_was_list_item and not is_list_item and not is_blank:
            out.append("")
        out.append(line)
        prev_was_list_item = is_list_item
    return "\n".join(out)
