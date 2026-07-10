"""Tests del razonamiento y convergencia de la IA (Ajuste 2A)."""
from app.services.campaign_learning import assess_maturity, build_analysis


def _camp(name, conv, cost, spend=10.0, ctr=1.5):
    return {"name": name, "conversations": conv, "cost_per_conv": cost, "spend": spend, "ctr": ctr}


def test_maturity_exploration_when_few_winners():
    camps = [_camp("a", 3, 2.0), _camp("b", 0, 5.0, spend=8.0)]
    m = assess_maturity(camps)
    assert m["converged"] is False
    assert m["mode"] == "exploration"


def test_maturity_converged_when_stable_cpl_and_own_wins():
    # Fase 1 (1.2): la convergencia exige, además del CPL estable, ≥2 campañas
    # PROPIAS de la IA verificadas como WIN por el outcome loop.
    camps = [_camp("a", 5, 2.0), _camp("b", 4, 2.1), _camp("c", 6, 1.9)]
    outcomes = [{"verdict": "WIN"}, {"verdict": "NEUTRAL"}, {"verdict": "WIN"}]
    m = assess_maturity(camps, outcomes)
    assert m["converged"] is True
    assert m["mode"] == "optimized"


def test_maturity_stable_cpl_without_own_wins_stays_exploring():
    # CPL estable pero la IA no tiene resultados propios verificados: el CPL
    # puede venir de campañas creadas a mano, no prueba el criterio de la IA.
    camps = [_camp("a", 5, 2.0), _camp("b", 4, 2.1), _camp("c", 6, 1.9)]
    m = assess_maturity(camps)  # sin outcomes
    assert m["converged"] is False
    assert m["mode"] == "exploration"
    assert "propia" in m["reason"]

    m1 = assess_maturity(camps, [{"verdict": "WIN"}])  # solo 1 WIN: no alcanza
    assert m1["converged"] is False


def test_maturity_two_recent_losses_force_exploration():
    # Cuenta convergida por CV, pero las últimas 2 creaciones de la IA fueron
    # LOSS → se fuerza exploración con razón explícita. NOT_ACTIVATED no cuenta
    # como veredicto (fallo de infraestructura, no de criterio).
    camps = [_camp("a", 5, 2.0), _camp("b", 4, 2.1), _camp("c", 6, 1.9)]
    outcomes = [
        {"verdict": "NOT_ACTIVATED"},
        {"verdict": "LOSS"},
        {"verdict": "LOSS"},
        {"verdict": "WIN"},
        {"verdict": "WIN"},
    ]
    m = assess_maturity(camps, outcomes)
    assert m["converged"] is False
    assert m["mode"] == "exploration"
    assert "no rindieron" in m["reason"]


def test_build_analysis_mentions_own_track_record():
    report = {"window_days": 7, "campaigns": [_camp("winner", 10, 1.5)]}
    maturity = {"converged": False, "mode": "exploration", "reason": "..."}
    analysis = build_analysis(
        report, best=_camp("winner", 10, 1.5), excluded_placements=[],
        best_device=None, best_age_range=None, suggested_budget=120.0,
        maturity=maturity,
        evaluated_outcomes=[{"verdict": "WIN"}, {"verdict": "LOSS"}, {"verdict": "NEUTRAL"}],
    )
    assert analysis["track_record"] == (
        "De las 3 campañas que la IA creó y ya evaluó, 1 funcionaron, "
        "1 no funcionaron y 1 quedaron neutrales."
    )
    assert analysis["track_record"] in analysis["summary"]


def test_maturity_exploration_when_cpl_volatile():
    camps = [_camp("a", 5, 1.0), _camp("b", 4, 5.0), _camp("c", 6, 9.0)]
    m = assess_maturity(camps)
    assert m["converged"] is False
    assert m["mode"] == "exploration"


def test_build_analysis_reports_past_failures_and_changes():
    report = {
        "window_days": 7,
        "campaigns": [
            _camp("winner", 10, 1.5),
            _camp("burned", 0, 0.0, spend=20.0),  # gastó sin conversiones
        ],
    }
    maturity = {"converged": False, "mode": "exploration", "reason": "..."}
    analysis = build_analysis(
        report,
        best=_camp("winner", 10, 1.5),
        excluded_placements=["instagram_reels"],
        best_device="mobile",
        best_age_range="25-34",
        suggested_budget=120.0,
        maturity=maturity,
    )
    assert analysis["summary"]
    assert any("burned" in f for f in analysis["past_failures"])
    assert any("Excluir" in c["change"] for c in analysis["changes_proposed"])
    assert analysis["evaluation_window_days"] == 7
    assert analysis["confidence"] in ("alta", "media", "baja")


def test_build_analysis_no_history_conservative_summary():
    report = {"window_days": 7, "campaigns": []}
    maturity = {"converged": False, "mode": "exploration", "reason": "..."}
    analysis = build_analysis(
        report, best=None, excluded_placements=[], best_device=None,
        best_age_range=None, suggested_budget=100.0, maturity=maturity,
    )
    assert "sin histórico" in analysis["summary"].lower() or "conservadora" in analysis["summary"].lower()
    assert analysis["confidence"] == "baja"
