"""Guarda estricta de respaldo numérico (grounding) para análisis del chat (AI-1).

Puro — sin BD, sin HTTP.

Compara valores estructurados (float) y clasificados por unidad (MONEY, PERCENT, COUNT, ANY),
eliminando colisiones de dígitos (ej. "27.00" vs "2700") y munición de IDs/timestamps opacos.
"""
from dataclasses import dataclass, is_dataclass, asdict
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

MAX_GROUNDING_VALUES = 220
MAX_ANALYSIS_CHARS = 2000

OPAQUE_KEYS = frozenset({
    "campaign_id",
    "external_account_id",
    "ad_account_id",
    "organization_id",
    "page_id",
    "external_page_id",
    "actor_id",
    "id",
    "created_at",
    "updated_at",
    "meta_created_time",
    "token",
    "image_hash",
    "hash",
})

_URL_RE = re.compile(r"https?://\S+")

# Regex para extraer tokens numéricos con prefijo de moneda o sufijo de unidad
_EXTRACT_TOKEN_RE = re.compile(
    r"(?P<prefix>S/\.?|\$|USD|PEN)?\s*(?P<num>\d[\d.,]*)(?:\s*(?P<suffix>%|por ciento|soles|dias|días))?",
    re.IGNORECASE
)


class GroundingUnit(str, Enum):
    MONEY = "MONEY"
    PERCENT = "PERCENT"
    COUNT = "COUNT"
    ANY = "ANY"


@dataclass(frozen=True)
class GroundingEntry:
    value: float
    unit: GroundingUnit


class GroundingSet:
    def __init__(self):
        self._entries: List[GroundingEntry] = []

    def add(self, value: float, unit: GroundingUnit = GroundingUnit.ANY) -> None:
        if len(self._entries) >= MAX_GROUNDING_VALUES:
            return
        val_rounded = round(float(value), 4)
        entry = GroundingEntry(value=val_rounded, unit=unit)
        if entry not in self._entries:
            self._entries.append(entry)

    def size(self) -> int:
        return len(self._entries)

    def contains(self, val: float, required_unit: GroundingUnit = GroundingUnit.ANY, epsilon: float = 0.001) -> bool:
        for entry in self._entries:
            # Compatibilidad de unidades
            if required_unit == GroundingUnit.MONEY and entry.unit not in (GroundingUnit.MONEY, GroundingUnit.ANY):
                continue
            if required_unit == GroundingUnit.PERCENT and entry.unit not in (GroundingUnit.PERCENT, GroundingUnit.ANY):
                continue
            if required_unit == GroundingUnit.COUNT and entry.unit not in (GroundingUnit.COUNT, GroundingUnit.ANY):
                continue

            # Comparación de valor flotante
            if abs(entry.value - val) <= epsilon:
                return True
        return False

    @classmethod
    def from_data(cls, data: Any) -> "GroundingSet":
        gset = cls()
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                # String plano: extraer números sueltos
                for m in re.finditer(r"\d[\d.,]*", data):
                    cand = _parse_float_candidates(m.group(0))
                    for c in cand:
                        gset.add(c, GroundingUnit.ANY)
                return gset

        cls._traverse_and_collect(data, gset)
        return gset

    @classmethod
    def _traverse_and_collect(cls, node: Any, gset: "GroundingSet", parent_key: str = "") -> None:
        if is_dataclass(node):
            node = asdict(node)

        if isinstance(node, dict):
            for k, v in node.items():
                if k in OPAQUE_KEYS:
                    continue
                cls._traverse_and_collect(v, gset, parent_key=k.lower())
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                cls._traverse_and_collect(item, gset, parent_key=parent_key)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            unit = cls._classify_key_unit(parent_key)
            gset.add(float(node), unit)
        elif isinstance(node, str):
            # Extraer números de strings descriptivos (ej. "los últimos 30 días" -> 30)
            for m in re.finditer(r"\d[\d.,]*", node):
                for c in _parse_float_candidates(m.group(0)):
                    gset.add(c, GroundingUnit.ANY)

    @staticmethod
    def _classify_key_unit(key: str) -> GroundingUnit:
        k = key.lower()
        if any(m in k for m in ("spend", "budget", "cpl", "cost", "amount", "cap", "inversion", "gasto", "precio")):
            return GroundingUnit.MONEY
        if any(p in k for p in ("ctr", "pct", "percent", "rate", "share", "delta_pct", "tasa")):
            return GroundingUnit.PERCENT
        if any(c in k for c in ("impressions", "clicks", "reach", "conversions", "conversations", "count", "days", "dias", "frequency", "leads")):
            return GroundingUnit.COUNT
        return GroundingUnit.ANY


def _parse_float_candidates(raw: str) -> List[float]:
    """Interpreta números en notaciones español e inglés."""
    raw = raw.strip().rstrip(".,")
    if not raw:
        return []
    candidates = []
    
    # 1. Sin separador de miles: 1085.47 o 1085,47
    if "." in raw and "," not in raw:
        try:
            candidates.append(float(raw))
        except ValueError:
            pass
    elif "," in raw and "." not in raw:
        try:
            candidates.append(float(raw.replace(",", ".")))
        except ValueError:
            pass
    elif "." in raw and "," in raw:
        # Formato 1,085.47 (inglés)
        if raw.rfind(".") > raw.rfind(","):
            try:
                candidates.append(float(raw.replace(",", "")))
            except ValueError:
                pass
        # Formato 1.085,47 (español)
        else:
            try:
                candidates.append(float(raw.replace(".", "").replace(",", ".")))
            except ValueError:
                pass
    else:
        try:
            candidates.append(float(raw))
        except ValueError:
            pass

    return candidates


def numbers_are_grounded(facts_data: Any, answer: str) -> bool:
    """Verifica si todos los números de la respuesta están respaldados en facts_data."""
    answer = (answer or "").strip()
    if not answer or len(answer) > MAX_ANALYSIS_CHARS:
        return False
    if _URL_RE.search(answer):
        return False

    gset = GroundingSet.from_data(facts_data)

    # Buscar todos los números en la respuesta
    matches = list(_EXTRACT_TOKEN_RE.finditer(answer))
    for m in matches:
        raw_num = m.group("num")
        prefix = (m.group("prefix") or "").strip()
        suffix = (m.group("suffix") or "").strip().lower()

        req_unit = GroundingUnit.ANY
        if prefix or suffix in ("soles", "pen", "usd"):
            req_unit = GroundingUnit.MONEY
        elif suffix in ("%", "por ciento"):
            req_unit = GroundingUnit.PERCENT

        candidates = _parse_float_candidates(raw_num)
        if not candidates:
            return False

        # Si ninguna interpretación numérica de este token tiene respaldo, rechazar
        if not any(gset.contains(c, required_unit=req_unit) for c in candidates):
            return False

    return True


def grounded_or_canonical(facts_data: Any, canonical: str, answer: str) -> str:
    """Devuelve la respuesta del modelo si cumple la guarda; si no, el texto canónico."""
    return answer.strip() if numbers_are_grounded(facts_data, answer) else canonical
