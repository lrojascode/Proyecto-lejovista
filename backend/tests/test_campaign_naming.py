"""Nomenclatura inferida del histórico (F3).

Los corpus de estos tests son nombres REALES sacados de `campaign_snapshots`
(dos cuentas con convenciones opuestas), no inventados: es justamente el choque
entre ambas lo que hacía que la plantilla fija desentonara en una de las dos.
"""
from datetime import datetime

from app.services.campaign_naming import (
    build_campaign_name,
    format_date_token,
    infer_naming_pattern,
)

# Cuenta tipo Kibo/MTV: "_", MAYÚSCULAS, token de tipo, fecha al final.
KIBO_NAMES = [
    "ALT_ABIERTA_UGC",
    "ALT_MSJ_LEADS_JUN26",
    "MTV_MSJ_CABLE_GPT_CERCADO_JUN2026",
    "MTV_MSJ_THHN_LIMA_JUN2026",
    "MTV_MSJ_VULCANIZADO_SURQUILLO_JUN2026",
]

# Cuenta tipo ITS: "-", marca con contador, espacios dentro de segmentos,
# sufijo fijo "CP", mes en español.
ITS_NAMES = [
    "JMM007-SAP SBO-NOV24-CP",
    "JMM029-SAP ABAP-ENE-CP",
    "JMM034-SAP GENERAL-CP",
    "JMM038-SAP TECNICO-CP",
    "JMM050-SAP BTP-JUN-CP",
]

JULY = datetime(2026, 7, 20)
JANUARY = datetime(2026, 1, 15)


def test_no_history_falls_back_to_the_old_template():
    """No-regresión: sin evidencia el nombre es el de siempre."""
    pattern = infer_naming_pattern([])
    name = build_campaign_name("Cable GPT", brand="MARCA", pattern=pattern, now=JULY)
    assert name == "MARCA_MSJ_CABLEGPT_JUL2026"


def test_two_names_are_not_enough_evidence():
    pattern = infer_naming_pattern(["A-B-C", "D-E-F"])
    assert pattern["sample_size"] == 0
    assert pattern["separator"] == "_"


def test_repeated_snapshots_do_not_count_as_evidence():
    """El histórico trae un snapshot por día y campaña: sin dedup, una sola
    campaña repetida 30 veces parecería una convención consolidada."""
    pattern = infer_naming_pattern(["ALT_MSJ_LEADS_JUN26"] * 30)
    assert pattern["sample_size"] == 0


def test_infers_underscore_convention():
    pattern = infer_naming_pattern(KIBO_NAMES)
    assert pattern["separator"] == "_"
    assert pattern["uppercase"] is True
    assert pattern["allow_spaces"] is False
    assert pattern["include_kind"] is True
    assert pattern["brand_counter"] is None


def test_builds_name_with_underscore_convention():
    pattern = infer_naming_pattern(KIBO_NAMES)
    name = build_campaign_name("Cable GPT", brand="MTV", pattern=pattern, now=JULY)
    assert name == "MTV_MSJ_CABLEGPT_JUL2026"


def test_infers_dash_convention_with_counter_and_suffix():
    pattern = infer_naming_pattern(ITS_NAMES)
    assert pattern["separator"] == "-"
    assert pattern["allow_spaces"] is True
    # Estos nombres no llevan token de tipo: meter "MSJ" los delataría.
    assert pattern["include_kind"] is False
    assert "CP" in pattern["trailing_tokens"]
    assert pattern["brand_counter"]["prefix"] == "JMM"
    assert pattern["brand_counter"]["next"] == 51  # continúa desde JMM050
    assert pattern["brand_counter"]["width"] == 3


def test_builds_name_continuing_the_counter():
    pattern = infer_naming_pattern(ITS_NAMES)
    name = build_campaign_name("SAP FINANZAS", brand="ITS", pattern=pattern, now=JULY)
    # Marca con contador continuado, espacios conservados y sufijo de la casa.
    # Mes sin año porque es lo que predomina en ese corpus (ENE, JUN).
    assert name == "JMM051-SAP FINANZAS-JUL-CP"


def test_date_token_uses_spanish_months():
    """`strftime("%b")` da 'JAN' en la locale del contenedor, pero el histórico
    escribe 'ENE'. El desfase solo se notaba fuera de los meses que coinciden."""
    assert format_date_token(infer_naming_pattern(ITS_NAMES), JANUARY) == "ENE"
    assert format_date_token(infer_naming_pattern(KIBO_NAMES), JANUARY) == "ENE2026"


def test_date_format_follows_history_width():
    """El ancho del sello se copia del histórico, no se impone: hay cuentas con
    año de 4 dígitos, otras de 2, y otras sin año."""
    assert format_date_token(infer_naming_pattern(KIBO_NAMES), JULY) == "JUL2026"

    two_digit_year = [
        "JSR036 - SAP SBO - ENE24 - CP",
        "JSR064 - SAP MANTENIMIENTO - ENE24 - CP",
        "JSR070 - SAP HANA - FEB24 - CP",
    ]
    assert format_date_token(infer_naming_pattern(two_digit_year), JULY) == "JUL26"

    assert format_date_token(infer_naming_pattern(ITS_NAMES), JULY) == "JUL"


def test_separator_inside_product_name_is_removed():
    """Un separador colado en el producto rompería el conteo de segmentos."""
    pattern = infer_naming_pattern(ITS_NAMES)
    name = build_campaign_name("SAP-BTP", brand="ITS", pattern=pattern, now=JULY)
    assert name.count("-") == len("JMM051-SAPBTP-JUL26-CP".split("-")) - 1


def test_spaced_dash_separator_is_preferred_over_bare_dash():
    """'JSR036 - SAP SBO - ENE24' se parte por ' - ', no por '-', o quedarían
    segmentos con espacios sueltos en los bordes."""
    names = [
        "JSR036 - SAP SBO - ENE24 - CP",
        "JSR064 - SAP MANTENIMIENTO - ENE24 - CP",
        "JSR070 - SAP HANA - FEB24 - CP",
    ]
    assert infer_naming_pattern(names)["separator"] == " - "
