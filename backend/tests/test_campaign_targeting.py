"""Tests del targeting optimizado del AdSet (Ajuste 2 — conexión IA→creación)."""
from app.services.campaign_targeting import (
    parse_age_range, device_targeting, build_targeting, _facebook_positions,
)


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
    t = build_targeting(best_age_range="18-24", best_device="android_tablet", excluded_placements=[])
    assert t["age_min"] == 18 and t["age_max"] == 24
    assert t["user_os"] == ["Android"]
    assert t["device_platforms"] == ["mobile"]
    assert t["publisher_platforms"] == ["facebook"]


def test_build_targeting_falls_back_without_history():
    t = build_targeting(best_age_range=None, best_device=None, excluded_placements=[])
    assert t["age_min"] == 25 and t["age_max"] == 65
    assert "user_os" not in t  # sin evidencia, no se narra dispositivo
    assert "feed" in t["facebook_positions"]


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
