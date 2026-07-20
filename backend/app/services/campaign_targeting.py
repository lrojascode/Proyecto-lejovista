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

# Meta impone DOS reglas sobre `targeting_automation.advantage_audience` que hay
# que satisfacer a la vez:
#   - `1870188`: no se admite `advantage_audience=1` con `age_min > 25` (con
#     Advantage+ la edad es solo una SUGERENCIA, no un control).
#   - `1870227`: el campo es OBLIGATORIO y debe declararse explícitamente como
#     1 o 0 — omitirlo también rechaza el adset.
# Es decir: siempre se manda, y vale 0 cuando la edad mínima pasa de 25.
_ADVANTAGE_AUDIENCE_MAX_AGE_MIN = 25

# Defaults de siempre, para cuentas sin histórico con qué optimizar.
_DEFAULT_AGE_MIN = 25
_DEFAULT_AGE_MAX = 65

# Posiciones de Instagram para campañas de mensajes. Vocabulario de la Marketing
# API v25 (`instagram_positions`): 'stream' = feed, 'explore_home' e 'ig_search'
# se dejan fuera porque rinden mal en click-to-WhatsApp.
_DEFAULT_INSTAGRAM_POSITIONS = ["stream", "story", "reels", "explore"]


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


def _norm_position(prefix: str, p: str) -> str:
    """'facebook_facebook_profile_feed' -> 'profile_feed'; 'instagram_stream' ->
    'stream'. El histórico nombra los placements como
    `{publisher_platform}_{platform_position}` (breakdown de Meta, ver
    `_BREAKDOWN_SPECS` en meta_ads/sync.py), y el targeting los quiere sin el
    prefijo de plataforma."""
    return re.sub(rf"^{prefix}[_ ]?", "", str(p).lower()).replace(f"{prefix}_", "").strip()


def _facebook_positions(excluded_placements: List[str], rules: Optional[Dict[str, Any]] = None) -> List[str]:
    """Allowlist de posiciones de Facebook menos las que el histórico marcó como
    desperdicio (gasto y 0 conversiones). Las `required_placements` del tenant
    (p. ej. facebook_reels) nunca se quitan.

    `rules` (M-2): reglas resueltas para la org (`get_campaign_rules_for_org`);
    por defecto usa `TENANT_CAMPAIGN_RULES` (compat con llamadas existentes)."""
    rules = rules or TENANT_CAMPAIGN_RULES
    base = ["feed", "story"] + list(rules["required_placements"])
    required = {p.lower() for p in rules["required_placements"]}

    def norm(p: str) -> str:
        return _norm_position("facebook", p)

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


def _instagram_positions(excluded_placements: List[str], rules: Optional[Dict[str, Any]] = None) -> List[str]:
    """Hermano de `_facebook_positions` para Instagram.

    Solo se descartan las posiciones que el histórico marcó como desperdicio
    (gasto y 0 conversiones), igual que en Facebook. OJO con la asimetría de
    vocabulario: en el breakdown de Meta las claves llegan como
    `instagram_stream`/`instagram_reels`, pero `instagram_positions` las quiere
    sin prefijo (`stream`, `reels`).
    """
    rules = rules or TENANT_CAMPAIGN_RULES
    base = list(rules.get("instagram_positions") or _DEFAULT_INSTAGRAM_POSITIONS)

    excl = {_norm_position("instagram", e) for e in (excluded_placements or [])}
    seen, out = set(), []
    for p in base:
        if p in seen:
            continue
        seen.add(p)
        if _norm_position("instagram", p) not in excl:
            out.append(p)
    # Meta rechaza `instagram_positions: []`. Si el histórico excluyó todo, se
    # omite el campo (Meta usa todas las posiciones por defecto) en vez de
    # mandar una lista vacía — lo resuelve `build_targeting`.
    return out


def build_targeting(
    *,
    best_age_range: Optional[str] = None,
    best_device: Optional[str] = None,
    excluded_placements: Optional[List[str]] = None,
    rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Targeting completo del AdSet a partir de las señales del histórico ganador,
    con fallback a los defaults conservadores de siempre.

    `rules` (M-2): reglas resueltas para la org (`get_campaign_rules_for_org`);
    por defecto usa `TENANT_CAMPAIGN_RULES` (compat con llamadas existentes)."""
    rules = rules or TENANT_CAMPAIGN_RULES
    age_min, age_max = parse_age_range(best_age_range)
    if age_min is None:
        age_min, age_max = _DEFAULT_AGE_MIN, _DEFAULT_AGE_MAX

    targeting: Dict[str, Any] = {
        "geo_locations": {"regions": [{"key": rules["default_geo_region_key"]}]},
        "age_min": age_min,
        "age_max": age_max,
        # Facebook + Instagram. Antes era solo facebook: la campaña nunca se
        # mostraba en IG aunque la página tuviera cuenta conectada y el
        # histórico midiera placements de Instagram.
        "publisher_platforms": ["facebook", "instagram"],
        "facebook_positions": _facebook_positions(excluded_placements or [], rules),
    }

    ig_positions = _instagram_positions(excluded_placements or [], rules)
    if ig_positions:
        targeting["instagram_positions"] = ig_positions

    # Advantage+ audience expande la segmentación, pero Meta NO lo admite junto a
    # una edad mínima > 25 (`1870188`). Cuando el histórico ganador apunta a un
    # público mayor (p. ej. 45-54, el caso real del 2026-07-17), se prioriza esa
    # señal —derivada del rendimiento real de la cuenta— como restricción DURA y
    # se apaga la expansión automática.
    # OJO: hay que apagarla con un 0 explícito, no omitiendo el campo: Meta lo
    # exige presente (`1870227`, "debes activar o desactivar la función").
    targeting["targeting_automation"] = {
        "advantage_audience": 1 if age_min <= _ADVANTAGE_AUDIENCE_MAX_AGE_MIN else 0
    }

    targeting.update(device_targeting(best_device))
    return targeting
