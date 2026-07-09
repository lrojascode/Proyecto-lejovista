"""Construcción del targeting optimizado del AdSet (Ajuste 2 — conexión IA→creación).

Antes `task_create_adset` hardcodeaba el público (edad 25‑65, sin dispositivo,
placements fijos), así que las optimizaciones que el `CampaignSuggester` deriva
del histórico ganador (mejor rango de edad, mejor dispositivo, placements a
excluir) se mostraban al usuario pero NUNCA se aplicaban a la campaña real.

Este módulo traduce esas señales al `targeting` de Meta, con fallback a los
defaults conservadores de siempre cuando no hay evidencia (cuenta sin histórico).

La GEO se mantiene deliberadamente en la región por defecto del tenant: mapear
la "zona" de texto (p. ej. "Surquillo") a una región/radio de Meta es una
decisión de producto aparte y no se infiere del histórico.
"""
import re
from typing import Any, Dict, List, Optional

from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES

# Límites de edad que acepta Meta (18 por políticas; 65 es el tope/"65+").
_AGE_FLOOR = 18
_AGE_CEIL = 65

# Defaults de siempre, para cuentas sin histórico con qué optimizar.
_DEFAULT_AGE_MIN = 25
_DEFAULT_AGE_MAX = 65


def parse_age_range(age_range: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """'25-34' -> (25, 34); '65+' -> (65, 65); None/desconocido -> (None, None)."""
    if not age_range:
        return None, None
    text = str(age_range).strip()
    m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})$", text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.match(r"^(\d{1,2})\s*\+$", text)  # '65+'
        if m2:
            lo, hi = int(m2.group(1)), _AGE_CEIL
        else:
            return None, None
    lo = max(_AGE_FLOOR, min(lo, _AGE_CEIL))
    hi = max(lo, min(hi, _AGE_CEIL))
    return lo, hi


def device_targeting(best_device: Optional[str]) -> Dict[str, Any]:
    """Traduce el dispositivo ganador (breakdown `impression_device` de Meta:
    'mobile', 'desktop', 'android_smartphone', 'android_tablet', 'iphone',
    'ipad'…) a targeting.

    Se narra a nivel de SISTEMA OPERATIVO / plataforma, no al modelo exacto de
    dispositivo: pinchar solo "android_tablet" dejaría una audiencia diminuta y
    arriesgada. Narrar a Android/iOS + móvil es una optimización defendible que
    no mata el alcance. Sin evidencia -> {} (no se narra nada)."""
    if not best_device:
        return {}
    d = str(best_device).lower()

    if "desktop" in d:
        return {"device_platforms": ["desktop"]}
    if "android" in d:
        return {"user_os": ["Android"], "device_platforms": ["mobile"]}
    if "iphone" in d or "ipad" in d or "ios" in d:
        return {"user_os": ["iOS"], "device_platforms": ["mobile"]}
    if "mobile" in d:
        return {"device_platforms": ["mobile"]}
    return {}


def _facebook_positions(excluded_placements: List[str]) -> List[str]:
    """Allowlist de posiciones de Facebook menos las que el histórico marcó como
    desperdicio (gasto y 0 conversiones). Las `required_placements` del tenant
    (p. ej. facebook_reels) nunca se quitan."""
    base = ["feed", "story"] + list(TENANT_CAMPAIGN_RULES["required_placements"])
    required = {p.lower() for p in TENANT_CAMPAIGN_RULES["required_placements"]}

    def norm(p: str) -> str:
        # 'facebook_facebook_profile_feed' -> 'profile_feed'; 'facebook_feed' -> 'feed'.
        return re.sub(r"^facebook[_ ]?", "", str(p).lower()).replace("facebook_", "").strip()

    excl = {norm(e) for e in (excluded_placements or [])}
    # Dedup preservando orden.
    seen, out = set(), []
    for p in base:
        if p in seen:
            continue
        seen.add(p)
        if p.lower() in required or norm(p) not in excl:
            out.append(p)
    return out


def build_targeting(
    *,
    best_age_range: Optional[str] = None,
    best_device: Optional[str] = None,
    excluded_placements: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Targeting completo del AdSet a partir de las señales del histórico ganador,
    con fallback a los defaults conservadores de siempre."""
    age_min, age_max = parse_age_range(best_age_range)
    if age_min is None:
        age_min, age_max = _DEFAULT_AGE_MIN, _DEFAULT_AGE_MAX

    targeting: Dict[str, Any] = {
        "geo_locations": {"regions": [{"key": TENANT_CAMPAIGN_RULES["default_geo_region_key"]}]},
        "age_min": age_min,
        "age_max": age_max,
        "publisher_platforms": ["facebook"],
        "facebook_positions": _facebook_positions(excluded_placements or []),
        # Advantage+ audience: Meta expande a partir de estas señales; la edad y
        # el dispositivo siguen actuando como restricción dura.
        "targeting_automation": {"advantage_audience": 1},
    }
    targeting.update(device_targeting(best_device))
    return targeting
