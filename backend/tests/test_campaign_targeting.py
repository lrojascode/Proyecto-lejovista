"""Tests del targeting optimizado del AdSet (Ajuste 2 — conexión IA→creación)."""
from app.services.campaign_targeting import (
    parse_age_range, device_targeting, build_targeting, _facebook_positions,
)


def _reglas_con_edad():
    """Reglas de una organización que SÍ quiere segmentar por edad.

    Por defecto está apagado (execution/05 §3): la data es plana entre rangos y
    segmentar obliga a apagar Advantage+, que es justo lo que conviene usar.
    """
    from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES
    return {**TENANT_CAMPAIGN_RULES, "use_age_targeting": True}


def test_parse_age_range_normal():
    assert parse_age_range("18-24") == (18, 24)
    assert parse_age_range("25-34") == (25, 34)


def test_parse_age_range_plus_and_clamps():
    assert parse_age_range("65+") == (65, 65)
    # Bajo el piso de 18 se sube a 18; sobre 65 se baja a 65.
    assert parse_age_range("13-17") == (18, 18)


def test_parse_age_range_unknown():
    assert parse_age_range(None) == (None, None)
    assert parse_age_range("adultos") == (None, None)


def test_device_targeting_android_and_ios():
    assert device_targeting("android_tablet") == {"user_os": ["Android"], "device_platforms": ["mobile"]}
    assert device_targeting("iphone") == {"user_os": ["iOS"], "device_platforms": ["mobile"]}
    assert device_targeting("desktop") == {"device_platforms": ["desktop"]}
    assert device_targeting("mobile") == {"device_platforms": ["mobile"]}


def test_device_targeting_none():
    assert device_targeting(None) == {}
    assert device_targeting("smart_fridge") == {}


def test_build_targeting_applies_winning_signals():
    t = build_targeting(best_age_range="18-24", best_device="android_tablet",
                        excluded_placements=[], rules=_reglas_con_edad())
    assert t["age_min"] == 18 and t["age_max"] == 24
    assert t["user_os"] == ["Android"]
    assert t["device_platforms"] == ["mobile"]
    assert t["publisher_platforms"] == ["facebook", "instagram"]


def test_build_targeting_includes_instagram():
    """Las campañas nuevas salen en Facebook Y Instagram (antes solo FB, aunque
    la página tuviera cuenta de IG conectada y el histórico midiera placements
    de Instagram)."""
    t = build_targeting(best_age_range=None, best_device=None, excluded_placements=[])
    assert t["publisher_platforms"] == ["facebook", "instagram"]
    assert t["instagram_positions"] == ["stream", "story", "reels", "explore"]


def test_instagram_positions_respect_history_exclusions():
    """El histórico llega como `instagram_stream` (breakdown de Meta) pero el
    targeting los quiere sin prefijo: 'stream'."""
    t = build_targeting(
        best_age_range=None, best_device=None,
        excluded_placements=["instagram_stream", "instagram_explore"],
    )
    assert t["instagram_positions"] == ["story", "reels"]
    # Excluir posiciones de IG no toca las de Facebook.
    assert "feed" in t["facebook_positions"]


def test_instagram_positions_omitted_when_all_excluded():
    """Meta rechaza `instagram_positions: []`; si el histórico excluyó todas, se
    omite el campo (Meta usa entonces todas las posiciones por defecto)."""
    t = build_targeting(
        best_age_range=None, best_device=None,
        excluded_placements=["instagram_stream", "instagram_story",
                             "instagram_reels", "instagram_explore"],
    )
    assert "instagram_positions" not in t
    assert t["publisher_platforms"] == ["facebook", "instagram"]


def test_build_targeting_falls_back_without_history():
    t = build_targeting(best_age_range=None, best_device=None, excluded_placements=[])
    assert t["age_min"] == 25 and t["age_max"] == 65
    assert "user_os" not in t  # sin evidencia, no se narra dispositivo
    assert "feed" in t["facebook_positions"]


# --- Advantage+ vs edad mínima. Meta impone dos reglas a la vez (caso real
# 2026-07-17, cuenta Agencia Kibo, histórico ganador 45-54):
#   1870188: no se admite advantage_audience=1 con age_min > 25.
#   1870227: el campo es obligatorio, hay que declararlo como 1 o 0 (omitirlo
#            también rechaza el adset).
# => siempre presente; 0 cuando la edad mínima pasa de 25. ---

def test_advantage_audience_disabled_when_winning_age_is_older():
    t = build_targeting(best_age_range="45-54", best_device=None, excluded_placements=[],
                        rules=_reglas_con_edad())
    # Se respeta la señal real del histórico como restricción dura...
    assert t["age_min"] == 45 and t["age_max"] == 54
    # ...apagando Advantage+ con un 0 EXPLÍCITO (no omitiendo el campo).
    assert t["targeting_automation"] == {"advantage_audience": 0}


def test_advantage_audience_always_declared():
    """1870227: el campo nunca puede faltar, sea cual sea la edad."""
    for age_range in ("18-24", "25-34", "45-54", "65+", None):
        t = build_targeting(best_age_range=age_range, best_device=None, excluded_placements=[])
        assert "targeting_automation" in t, f"falta el campo con {age_range}"
        assert t["targeting_automation"]["advantage_audience"] in (0, 1)


def test_advantage_audience_kept_for_young_audience():
    t = build_targeting(best_age_range="18-24", best_device=None, excluded_placements=[],
                        rules=_reglas_con_edad())
    assert t["age_min"] == 18
    assert t["targeting_automation"] == {"advantage_audience": 1}


def test_advantage_audience_kept_at_the_boundary():
    """25 es el límite que acepta Meta, no debe apagarse."""
    t = build_targeting(best_age_range="25-34", best_device=None, excluded_placements=[],
                        rules=_reglas_con_edad())
    assert t["age_min"] == 25
    assert t["targeting_automation"] == {"advantage_audience": 1}


def test_advantage_audience_kept_on_default_fallback():
    """Sin histórico el default es age_min=25 → Advantage+ sigue activo (el
    comportamiento de siempre para cuentas nuevas)."""
    t = build_targeting(best_age_range=None, best_device=None, excluded_placements=[])
    assert t["targeting_automation"] == {"advantage_audience": 1}


def test_excluded_placements_drop_positions_but_keep_required():
    # Excluir 'facebook_feed' quita 'feed' del allowlist...
    positions = _facebook_positions(["facebook_feed"])
    assert "feed" not in positions
    # ...pero nunca quita el placement requerido (facebook_reels).
    assert "facebook_reels" in positions


def test_excluded_placements_ignore_non_facebook():
    # Placements de otras plataformas no afectan las posiciones de Facebook.
    positions = _facebook_positions(["instagram_instagram_search", "audience_network_an_classic"])
    assert "feed" in positions and "story" in positions


# --- M-2: reglas por tenant (parámetro `rules` opcional, override por org) ---

def test_build_targeting_uses_custom_rules_geo_and_placements():
    custom_rules = {
        "default_geo_region_key": "9999",
        "required_placements": ["facebook_marketplace"],
    }
    t = build_targeting(excluded_placements=[], rules=custom_rules)
    assert t["geo_locations"]["regions"] == [{"key": "9999"}]
    assert "facebook_marketplace" in t["facebook_positions"]
    assert "facebook_reels" not in t["facebook_positions"]  # no es el required de este tenant


def test_facebook_positions_uses_custom_required_placements():
    custom_rules = {"required_placements": ["facebook_marketplace"]}
    positions = _facebook_positions(["facebook_marketplace"], rules=custom_rules)
    # required_placements nunca se excluye, ni siquiera si el histórico lo marcó.
    assert "facebook_marketplace" in positions


# --- Por defecto NO se segmenta por edad (execution/05 §3) ---

def test_por_defecto_se_ignora_el_rango_ganador():
    """El documento de ITS: "nunca segmentar por edad o género. La data es plana
    (S/1.83 a S/2.68 entre extremos). Público amplio + Advantage+ y listo."."""
    t = build_targeting(best_age_range="45-54", best_device=None, excluded_placements=[])
    assert (t["age_min"], t["age_max"]) != (45, 54)
    # Y lo importante: con público amplio, Advantage+ deja de ser un caso
    # especial y pasa a ser lo normal. Segmentar por edad lo DESACTIVABA.
    assert t["targeting_automation"] == {"advantage_audience": 1}


def test_la_regla_de_edad_se_puede_reactivar_por_organizacion():
    """No es un cambio global silencioso: una org que hoy depende de su rango
    ganador puede volver a encenderla."""
    t = build_targeting(best_age_range="45-54", best_device=None, excluded_placements=[],
                        rules=_reglas_con_edad())
    assert t["age_min"] == 45 and t["age_max"] == 54


# --- Geografía por países (execution/05 §2) ---

def test_geo_por_defecto_sigue_siendo_la_region():
    assert build_targeting()["geo_locations"] == {"regions": [{"key": "2771"}]}


def test_geo_por_paises_para_campanas_latam():
    """La app entendía SOLO una clave de región, así que una campaña LATAM era
    imposible de armar."""
    from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES
    reglas = {**TENANT_CAMPAIGN_RULES, "geo_countries": ["pe", "CL", " co "]}
    geo = build_targeting(rules=reglas)["geo_locations"]
    assert geo == {"countries": ["PE", "CL", "CO"]}
    assert "regions" not in geo
