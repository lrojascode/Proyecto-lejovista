"""Tests de la evaluación dinámica de riesgo de crear una campaña (Ajuste 1)."""
from app.services.campaign_risk import assess_campaign_risk


def _sug(budget=100.0, has_history=True, excluded=None):
    return {
        "suggested_budget": budget,
        "has_history": has_history,
        "excluded_placements": excluded or [],
    }


def test_low_risk_within_suggested_budget_and_history():
    r = assess_campaign_risk(budget=100, copy_text="oferta buena", suggestion=_sug())
    assert r["level"] == "LOW"
    assert any("PAUSADA" in reason for reason in r["reasons"])


def test_high_risk_budget_more_than_double():
    r = assess_campaign_risk(budget=250, copy_text="x", suggestion=_sug(budget=100))
    assert r["level"] == "HIGH"
    assert any("doble" in reason for reason in r["reasons"])


def test_high_risk_absolute_budget():
    r = assess_campaign_risk(budget=600, copy_text="x", suggestion=_sug(budget=1000))
    assert r["level"] == "HIGH"


def test_high_risk_policy_flagged_copy():
    r = assess_campaign_risk(budget=100, copy_text="el mejor medicamento", suggestion=_sug())
    assert r["level"] == "HIGH"
    assert any("Meta suele rechazar" in reason for reason in r["reasons"])


def test_medium_risk_no_history():
    r = assess_campaign_risk(budget=100, copy_text="x", suggestion=_sug(budget=0, has_history=False))
    assert r["level"] == "MEDIUM"
    assert any("histórico" in reason for reason in r["reasons"])


def test_medium_risk_budget_slightly_over_suggested():
    # 1.2x < ratio <= 2x, con histórico → MEDIUM (no puede ser LOW).
    r = assess_campaign_risk(budget=150, copy_text="x", suggestion=_sug(budget=100))
    assert r["level"] == "MEDIUM"


def test_inactive_account_is_high():
    r = assess_campaign_risk(budget=100, copy_text="x", suggestion=_sug(), account_status=2)
    assert r["level"] == "HIGH"
