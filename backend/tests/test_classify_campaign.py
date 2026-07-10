"""C-2: clasificador único de campañas, relativo al target_cpl dinámico —
antes report_builder.py, llm.py (narrativa mock) y llm.py (HTML brief, ya
eliminado en A-6) tenían cada uno su propio umbral absoluto en soles, y la
MISMA campaña podía clasificar distinto en cada pantalla.
"""
import pytest
from app.services.ai.thresholds import classify_campaign, compute_target_cpl, DEFAULT_THRESHOLDS


@pytest.mark.parametrize(
    "cost_per_conv,conversions,target_cpl,expected",
    [
        (0.0, 0, 5.0, "PAUSAR"),      # sin conversiones, independiente del costo
        (10.0, 0, 5.0, "PAUSAR"),
        (3.5, 5, 5.0, "GANADORA"),    # <= target * 0.7 (3.5)
        (5.0, 5, 5.0, "BUENA"),       # <= target * 1.0
        (9.9, 5, 5.0, "REGULAR"),     # <= target * cpl_critical_multiplier (10.0)
        (10.01, 5, 5.0, "CARA"),      # > target * 2.0
        (2.0, 3, 0.0, "GANADORA"),    # target_cpl inválido -> cae al fallback (3.50): 2.0 <= 3.5*0.7=2.45
    ],
)
def test_classify_campaign_table(cost_per_conv, conversions, target_cpl, expected):
    assert classify_campaign(cost_per_conv, conversions, target_cpl) == expected


def test_classify_campaign_boundaries_use_thresholds_constants():
    t = DEFAULT_THRESHOLDS
    target = 4.0
    ganadora_edge = round(target * t.label_ganadora_multiplier, 4)
    buena_edge = round(target * t.label_buena_multiplier, 4)
    regular_edge = round(target * t.cpl_critical_multiplier, 4)

    assert classify_campaign(ganadora_edge, 1, target) == "GANADORA"
    assert classify_campaign(ganadora_edge + 0.01, 1, target) == "BUENA"
    assert classify_campaign(buena_edge, 1, target) == "BUENA"
    assert classify_campaign(buena_edge + 0.01, 1, target) == "REGULAR"
    assert classify_campaign(regular_edge, 1, target) == "REGULAR"
    assert classify_campaign(regular_edge + 0.01, 1, target) == "CARA"


def test_compute_target_cpl_uses_floor_and_ceiling():
    t = DEFAULT_THRESHOLDS
    # Muy por debajo del piso -> se acota al piso.
    assert compute_target_cpl(total_spend=1.0, total_conversions=100, t=t) == t.target_cpl_floor
    # Muy por encima del techo -> se acota al techo.
    assert compute_target_cpl(total_spend=10000.0, total_conversions=10, t=t) == t.target_cpl_ceiling
    # Dentro del rango -> el promedio real.
    assert compute_target_cpl(total_spend=100.0, total_conversions=25, t=t) == 4.0


def test_compute_target_cpl_fallback_without_conversions():
    assert compute_target_cpl(total_spend=500.0, total_conversions=0) == DEFAULT_THRESHOLDS.target_cpl_fallback
