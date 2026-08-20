"""Detección del período sobre el que pregunta el usuario (CH-8). Puro — sin
BD, sin HTTP, sin LLM.

Antes toda respuesta hablaba de los últimos 7 días y no había forma de pedir
otra cosa. Acá se traduce "este mes", "los últimos 30 días" o "desde que
empecé" a una cantidad de días hacia atrás.

El número siempre sale del texto que escribió la persona: o de un dígito
literal ("los últimos 45 días") o de una frase de un catálogo cerrado ("el mes
pasado"). El LLM puede sugerir un período, pero solo eligiendo de un enum
cerrado (`PERIOD_ENUM`), nunca proponiendo un entero — misma disciplina que
`strip_numeric_slots_without_digit_evidence` en `extraction.py`.
"""
import re
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.services.campaign_chat.slots import strip_accents

# Techo real: más atrás de esto los snapshots se mueven a las tablas de archivo
# (`snapshot_archiver`), que ni `ReportBuilder` ni `kpi_period` consultan.
MAX_HISTORY_DAYS = settings.CHAT_MAX_HISTORY_DAYS

# Ventana por defecto cuando la pregunta no menciona ningún período. 7 días es
# lo que muestran el Resumen y la lista de campañas: si alguien pregunta
# "¿cuánto gasté?" mirando el dashboard, tiene que ver el mismo número.
DEFAULT_WINDOW_DAYS = 7


@dataclass(frozen=True)
class Period:
    days: int
    # Cómo nombrarlo en la respuesta ("este mes", "los últimos 45 días"). Se usa
    # tal cual en el texto para que el número nunca quede sin contexto.
    label: str


# Catálogo cerrado que puede elegir el LLM. Son etiquetas, no cifras: el modelo
# no puede pedir "los últimos 37 días" ni acercarse a inventar un número.
PERIOD_ENUM = {
    "7d": Period(7, "los últimos 7 días"),
    "30d": Period(30, "los últimos 30 días"),
    "90d": Period(90, "los últimos 90 días"),
    "180d": Period(MAX_HISTORY_DAYS, "todo el historial que tengo"),
    "all": Period(MAX_HISTORY_DAYS, "todo el historial que tengo"),
}

_UNIT_DAYS = {"dia": 1, "dias": 1, "semana": 7, "semanas": 7, "mes": 30, "meses": 30, "ano": 365, "anos": 365}

# "los últimos 45 días", "las ultimas 3 semanas", "hace 2 meses"
_EXPLICIT_RE = re.compile(
    r"(?:ultim[oa]s?|pasad[oa]s?|hace)\s+(\d{1,3})\s*(dias?|semanas?|mes(?:es)?|anos?)"
    r"|(\d{1,3})\s*(dias?|semanas?|mes(?:es)?|anos?)\s*(?:atras|antes)?",
    re.IGNORECASE,
)

# Frases sin dígitos, en orden de prioridad: la primera que matchea gana. Las
# de "pasado/pasada" van ANTES que las de período actual, porque "la semana
# pasada" contiene "la semana" y si no, ganaría la equivocada.
_NAMED_PERIODS = [
    (re.compile(r"\bhoy\b|\bde hoy\b", re.IGNORECASE), Period(1, "hoy")),
    (re.compile(r"\bayer\b", re.IGNORECASE), Period(2, "ayer y hoy")),
    (re.compile(r"semana pasada|ultima semana", re.IGNORECASE), Period(14, "las últimas 2 semanas")),
    (re.compile(r"mes pasado|ultimo mes", re.IGNORECASE), Period(60, "los últimos 2 meses")),
    (re.compile(r"\b(esta|la)\s+semana\b|semana actual", re.IGNORECASE), Period(7, "esta semana")),
    (re.compile(r"\b(este|el)\s+mes\b|mes actual|del mes", re.IGNORECASE), Period(30, "este mes")),
    (re.compile(r"\b(este|el)\s+(ano|trimestre)\b|ultimo trimestre", re.IGNORECASE),
     Period(90, "los últimos 90 días")),
    (re.compile(
        r"desde (que )?(empec|arranqu|el inicio|siempre)|todo el (historial|tiempo)|"
        r"historico completo|\bsiempre\b|\bhistorial\b|en total|desde el principio",
        re.IGNORECASE,
    ), Period(MAX_HISTORY_DAYS, "todo el historial que tengo")),
]


def _plural(cantidad: int, singular: str) -> str:
    return singular if cantidad == 1 else singular + "s"


def parse_period(text: str) -> Optional[Period]:
    """Período mencionado en el mensaje, o `None` si no menciona ninguno (en
    cuyo caso el motor usa `DEFAULT_WINDOW_DAYS`)."""
    ascii_text = strip_accents(text or "").lower()
    if not ascii_text.strip():
        return None

    match = _EXPLICIT_RE.search(ascii_text)
    if match:
        cantidad = int(match.group(1) or match.group(3))
        unidad = (match.group(2) or match.group(4) or "").rstrip()
        factor = _UNIT_DAYS.get(unidad, _UNIT_DAYS.get(unidad.rstrip("es"), 0))
        if cantidad > 0 and factor:
            dias = min(cantidad * factor, MAX_HISTORY_DAYS)
            unidad_legible = {1: "día", 7: "semana", 30: "mes", 365: "año"}[factor]
            etiqueta = (
                f"los últimos {cantidad} {_plural(cantidad, unidad_legible)}"
                if dias < MAX_HISTORY_DAYS
                else "todo el historial que tengo"
            )
            return Period(dias, etiqueta)

    for pattern, period in _NAMED_PERIODS:
        if pattern.search(ascii_text):
            return period
    return None


def period_from_enum(value: Optional[str]) -> Optional[Period]:
    """Traduce la etiqueta que devolvió el LLM. Cualquier cosa fuera del
    catálogo se descarta en silencio."""
    return PERIOD_ENUM.get(value) if isinstance(value, str) else None


def clamp_to_available(period: Period, days_available: int) -> Period:
    """Recorta el período a lo que realmente hay sincronizado, y lo dice en la
    etiqueta. Responder "en los últimos 90 días" cuando solo hay 10 días de
    datos es dar por ceros unos días que nunca se midieron."""
    if days_available <= 0 or days_available >= period.days:
        return period
    return Period(
        days_available,
        f"los últimos {days_available} {_plural(days_available, 'día')} "
        "(es todo lo que tengo sincronizado)",
    )
