"""Nomenclatura de campañas derivada del histórico REAL de la cuenta (F3).

Antes el nombre salía de una plantilla fija — `MARCA_MSJ_PRODUCTO_JUL2026`, la
del playbook §8 — que encaja con unas cuentas y desentona en otras. Los nombres
reales en BD muestran dos convenciones muy distintas dentro de la misma app:

    ALT_MSJ_LEADS_JUN26                    <- separador "_", con token de tipo
    MTV_MSJ_CABLE_GPT_CERCADO_JUN2026         MAYÚSCULAS, sin espacios
    JMM007-SAP SBO-NOV24-CP                <- separador "-", contador en la marca,
    JSR036 - SAP SBO - ENE24 - CP             espacios dentro de los segmentos,
    JMM050-SAP BTP-JUN-CP                     sufijo fijo "CP"

Meter una campaña `ITS_MSJ_SAPBTP_JUL2026` en la segunda cuenta canta a
kilómetros. Este módulo infiere el molde y lo aplica.

Todo es DETERMINISTA (sin LLM, regla AI-1): son reglas de mayoría sobre cadenas,
no interpretación. Y cuando no hay evidencia suficiente (< `_MIN_SAMPLES`
nombres) devuelve el patrón por defecto, que reproduce EXACTAMENTE la plantilla
de siempre — misma propiedad de despliegue seguro que R1/R2 en el motor de IA.
"""
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

# Con menos nombres que esto, cualquier "patrón" sería ruido, no convención.
_MIN_SAMPLES = 3

# Meses en español: el histórico usa ENE/SET/DIC, no los de `strftime("%b")`,
# que en la locale C del contenedor salen en inglés (JAN/SEP/DEC). Con la
# plantilla vieja el desfase solo se notaba fuera de los meses que coinciden
# (JUL, MAR...), así que pasaba desapercibido medio año.
_MONTHS_ES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
              "JUL", "AGO", "SET", "OCT", "NOV", "DIC"]
# Variantes que hay que RECONOCER al leer el histórico (SEP y SET conviven).
_MONTH_TOKENS = set(_MONTHS_ES) | {"SEP", "AGOS", "DICI"}

_SEPARATORS = [" - ", "_", "-", " · ", " "]

_DEFAULT_PATTERN: Dict[str, Any] = {
    "separator": "_",
    "uppercase": True,
    "date_format": "MMMYYYY",
    "allow_spaces": False,
    "include_kind": True,
    "trailing_tokens": [],
    "brand_counter": None,
    "sample_size": 0,
}


def _detect_separator(names: List[str]) -> str:
    """Separador dominante. Se prueban primero los más específicos (' - ' antes
    que '-') para no partir `JSR036 - SAP SBO` en trozos con espacios sueltos."""
    scores: Counter = Counter()
    for name in names:
        for sep in _SEPARATORS:
            if sep in name:
                scores[sep] += name.count(sep)
                break  # el primero que aparece gana: lista ordenada por especificidad
    return scores.most_common(1)[0][0] if scores else _DEFAULT_PATTERN["separator"]


def _parse_date_token(token: str) -> Optional[str]:
    """'JUN26' -> 'MMMYY'; 'JUN2026' -> 'MMMYYYY'; 'JUN' -> 'MMM'. None si no
    parece un sello de fecha."""
    m = re.fullmatch(r"([A-Z]{3,4})(\d{0,4})", token.strip().upper())
    if not m or m.group(1) not in _MONTH_TOKENS:
        return None
    digits = m.group(2)
    if len(digits) == 4:
        return "MMMYYYY"
    if len(digits) == 2:
        return "MMMYY"
    if not digits:
        return "MMM"
    return None


def _detect_brand_counter(first_segments: List[str]) -> Optional[Dict[str, Any]]:
    """Convención `JMM007` / `JSR036`: prefijo alfabético + contador correlativo.

    Se replica continuando la serie (`JMM080`) porque es justo lo que hace que
    esos nombres se vean "de la casa". Los nombres no son únicos en Meta, así
    que un choque de numeración es cosmético, no un error.
    """
    matches = [re.fullmatch(r"([A-Z]{2,5})(\d{2,4})", s.strip().upper()) for s in first_segments]
    hits = [m for m in matches if m]
    if len(hits) < max(_MIN_SAMPLES, len(first_segments) * 0.6):
        return None

    prefix = Counter(m.group(1) for m in hits).most_common(1)[0][0]
    same_prefix = [m for m in hits if m.group(1) == prefix]
    if not same_prefix:
        return None
    width = Counter(len(m.group(2)) for m in same_prefix).most_common(1)[0][0]
    return {
        "prefix": prefix,
        "next": max(int(m.group(2)) for m in same_prefix) + 1,
        "width": width,
    }


def infer_naming_pattern(names: List[str]) -> Dict[str, Any]:
    """Molde de nombres de la cuenta a partir de sus campañas reales.

    `names` son los `CampaignSnapshot.campaign_name` del histórico. Con menos de
    `_MIN_SAMPLES` devuelve el patrón por defecto (= plantilla de siempre).
    """
    clean = [n.strip() for n in (names or []) if n and n.strip()]
    # Dedup preservando orden: el histórico trae un snapshot por día y campaña.
    seen, unique = set(), []
    for n in clean:
        if n.upper() not in seen:
            seen.add(n.upper())
            unique.append(n)

    if len(unique) < _MIN_SAMPLES:
        return dict(_DEFAULT_PATTERN)

    separator = _detect_separator(unique)
    split = [[s.strip() for s in n.split(separator) if s.strip()] for n in unique]

    date_formats: Counter = Counter()
    trailing: Counter = Counter()
    kind_hits = 0
    for segments in split:
        for seg in segments:
            fmt = _parse_date_token(seg)
            if fmt:
                date_formats[fmt] += 1
                break
        if any(seg.upper() in {"MSJ", "PRB", "TRAF"} for seg in segments):
            kind_hits += 1
        # Sufijo fijo tipo "CP": último segmento corto, alfabético y sin fecha.
        if len(segments) > 1:
            last = segments[-1].upper()
            if len(last) <= 4 and last.isalpha() and not _parse_date_token(last):
                trailing[last] += 1

    total = len(unique)
    return {
        "separator": separator,
        # `.isupper()` es False si no hay ninguna letra mayúscula posible.
        "uppercase": sum(1 for n in unique if n.upper() == n) >= total * 0.6,
        "date_format": date_formats.most_common(1)[0][0] if date_formats else None,
        "allow_spaces": sum(1 for n in unique if " " in n.replace(separator, "")) >= total * 0.4,
        "include_kind": kind_hits >= total * 0.5,
        "trailing_tokens": [tok for tok, n in trailing.items() if n >= total * 0.4],
        "brand_counter": _detect_brand_counter([s[0] for s in split if s]),
        "sample_size": total,
    }


def format_date_token(pattern: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """Sello de fecha en el formato del histórico ('JUN26', 'JUN2026', 'JUN')."""
    now = now or datetime.now()
    month = _MONTHS_ES[now.month - 1]
    fmt = pattern.get("date_format")
    if fmt == "MMMYY":
        return f"{month}{now.year % 100:02d}"
    if fmt == "MMM":
        return month
    return f"{month}{now.year}"


def build_campaign_name(
    product_name: str,
    *,
    brand: str,
    pattern: Optional[Dict[str, Any]] = None,
    kind: str = "MSJ",
    now: Optional[datetime] = None,
) -> str:
    """Nombre de campaña con el molde de la cuenta.

    `brand` es el código derivado de la cuenta (`derive_brand_code`); si el
    histórico usa marca con contador (`JMM007`), ese gana y se continúa la serie.
    """
    pattern = pattern or _DEFAULT_PATTERN
    sep = pattern.get("separator") or "_"

    counter = pattern.get("brand_counter")
    if counter:
        brand_segment = f"{counter['prefix']}{counter['next']:0{counter['width']}d}"
    else:
        brand_segment = brand

    product = product_name.strip()
    if not pattern.get("allow_spaces", False):
        product = re.sub(r"\s+", "", product)
    # El separador dentro del nombre del producto rompería el conteo de segmentos.
    product = product.replace(sep.strip() or sep, "")

    segments = [brand_segment]
    if pattern.get("include_kind", True):
        segments.append(kind)
    segments.append(product)
    if pattern.get("date_format"):
        segments.append(format_date_token(pattern, now))
    segments.extend(pattern.get("trailing_tokens") or [])

    name = sep.join(s for s in segments if s)
    return name.upper() if pattern.get("uppercase", True) else name
