"""Tests del razonamiento y convergencia de la IA (Ajuste 2A)."""
from app.services.campaign_learning import (
    assess_maturity, build_analysis, calibrated_cost_per_conv, latest_winning_params,
)


def _camp(name, conv, cost, spend=10.0, ctr=1.5):
    return {"name": name, "conversations": conv, "cost_per_conv": cost, "spend": spend, "ctr": ctr}


def _res(verdict, cost=None, params=None, reason="motivo"):
    """Resultado propio ya evaluado, en la forma que devuelve
    `CampaignSuggester._own_evaluated_outcomes`: par params + outcome."""
    return {
        "params": params or {},
        "outcome": {"verdict": verdict, "cost_per_conv": cost, "reason": reason},
    }


def test_maturity_exploration_when_few_winners():
    camps = [_camp("a", 3, 2.0), _camp("b", 0, 5.0, spend=8.0)]
    m = assess_maturity(camps)
    assert m["converged"] is False
    assert m["mode"] == "exploration"


def test_maturity_converged_when_stable_cpl_and_own_wins():
    # Fase 1 (1.2): la convergencia exige, además del CPL estable, ≥2 campañas
    # PROPIAS de la IA verificadas como WIN por el outcome loop.
    camps = [_camp("a", 5, 2.0), _camp("b", 4, 2.1), _camp("c", 6, 1.9)]
    outcomes = [_res("WIN"), _res("NEUTRAL"), _res("WIN")]
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

    m1 = assess_maturity(camps, [_res("WIN")])  # solo 1 WIN: no alcanza
    assert m1["converged"] is False


def test_maturity_two_recent_losses_force_exploration():
    # Cuenta convergida por CV, pero las últimas 2 creaciones de la IA fueron
    # LOSS → se fuerza exploración con razón explícita. NOT_ACTIVATED no cuenta
    # como veredicto (fallo de infraestructura, no de criterio).
    camps = [_camp("a", 5, 2.0), _camp("b", 4, 2.1), _camp("c", 6, 1.9)]
    outcomes = [
        _res("NOT_ACTIVATED"),
        _res("LOSS"),
        _res("LOSS"),
        _res("WIN"),
        _res("WIN"),
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
        evaluated_outcomes=[_res("WIN"), _res("LOSS"), _res("NEUTRAL")],
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


# ═══ El eslabón del aprendizaje: el resultado medido cambia la propuesta ═══
# Antes, los veredictos solo alimentaban texto explicativo: la IA proponía lo
# mismo con 5 victorias que con 5 derrotas.

# --- R1: calibrar el costo esperado con lo que la IA realmente logró ---

def test_calibrated_cost_is_median_of_own_measured_results():
    # Incluye LOSS y NEUTRAL a propósito: quedarse solo con las WIN sería sesgo
    # de supervivencia y devolvería otra vez una expectativa optimista.
    results = [_res("WIN", 2.0), _res("LOSS", 8.0), _res("NEUTRAL", 4.0)]
    assert calibrated_cost_per_conv(results) == 4.0


def test_calibrated_cost_ignores_losses_without_measurable_cost():
    """Un LOSS con 0 conversaciones no tiene costo medible (None): no aporta."""
    results = [_res("WIN", 2.0), _res("LOSS", None), _res("WIN", 3.0)]
    assert calibrated_cost_per_conv(results) == 2.5


def test_calibrated_cost_none_without_own_results():
    """Sin resultados propios el sugeridor debe caer al comportamiento de siempre."""
    assert calibrated_cost_per_conv([]) is None
    assert calibrated_cost_per_conv(None) is None
    assert calibrated_cost_per_conv([_res("NOT_ACTIVATED")]) is None


def test_an_expensive_loss_raises_the_calibrated_cost():
    """Aprendizaje real: tras un mal resultado la expectativa se vuelve más
    conservadora (y el presupuesto sube), sin culpar a ningún parámetro."""
    barato = calibrated_cost_per_conv([_res("WIN", 2.0), _res("WIN", 2.2)])
    tras_loss = calibrated_cost_per_conv([_res("LOSS", 9.0), _res("WIN", 2.0), _res("WIN", 2.2)])
    assert tras_loss > barato


# --- R2: replicar la configuración ganadora propia ---

def test_latest_winning_params_returns_most_recent_win():
    """La lista viene del más reciente al más viejo."""
    results = [
        _res("LOSS", 9.0, params={"best_age_range": "18-24"}),
        _res("WIN", 2.0, params={"best_age_range": "45-54", "best_device": "android_smartphone"}),
        _res("WIN", 2.5, params={"best_age_range": "25-34"}),
    ]
    win = latest_winning_params(results)
    assert win["best_age_range"] == "45-54"
    assert win["best_device"] == "android_smartphone"


def test_latest_winning_params_none_without_wins():
    assert latest_winning_params([_res("LOSS", 9.0), _res("NEUTRAL", 4.0)]) is None
    assert latest_winning_params([]) is None


# --- R3: los fallos propios se explican (sin inventar causalidad) ---

def test_own_losses_appear_in_past_failures():
    report = {"window_days": 7, "campaigns": [_camp("winner", 10, 1.5)]}
    maturity = {"converged": False, "mode": "exploration", "reason": "..."}
    analysis = build_analysis(
        report, best=_camp("winner", 10, 1.5), excluded_placements=[],
        best_device=None, best_age_range=None, suggested_budget=120.0,
        maturity=maturity,
        evaluated_outcomes=[_res(
            "LOSS", 9.0,
            params={"best_age_range": "18-24", "best_device": "desktop", "budget": 70.0},
            reason="S/.9.00 por conversación: más del doble del costo esperado.",
        )],
    )
    fallo = next(f for f in analysis["past_failures"] if "propia" in f)
    assert "edad 18-24" in fallo and "dispositivo desktop" in fallo
    assert "más del doble" in fallo
    assert "no se replica" in fallo


def test_budget_explanation_says_it_was_calibrated():
    report = {"window_days": 7, "campaigns": [_camp("w", 10, 1.5)]}
    maturity = {"converged": False, "mode": "exploration", "reason": "..."}
    analysis = build_analysis(
        report, best=_camp("w", 10, 1.5), excluded_placements=[],
        best_device=None, best_age_range=None, suggested_budget=80.0,
        maturity=maturity, evaluated_outcomes=[_res("WIN", 4.0)],
        calibrated_cost=4.0,
    )
    budget_change = next(c for c in analysis["changes_proposed"] if "Presupuesto" in c["change"])
    assert "costo real que la IA ha logrado" in budget_change["why"]
    assert "no el mejor caso histórico" in budget_change["why"]


def test_budget_explanation_is_honest_without_own_results():
    """Sin resultados propios NO debe afirmar que calibró con ellos."""
    report = {"window_days": 7, "campaigns": [_camp("w", 10, 1.5)]}
    maturity = {"converged": False, "mode": "exploration", "reason": "..."}
    analysis = build_analysis(
        report, best=_camp("w", 10, 1.5), excluded_placements=[],
        best_device=None, best_age_range=None, suggested_budget=30.0,
        maturity=maturity, evaluated_outcomes=[],
    )
    budget_change = next(c for c in analysis["changes_proposed"] if "Presupuesto" in c["change"])
    assert "Aún no hay resultados propios medidos" in budget_change["why"]


def test_summary_claims_replication_only_when_it_really_replicated():
    report = {"window_days": 7, "campaigns": [_camp("w", 10, 1.5)]}
    maturity = {"converged": True, "mode": "optimized", "reason": "..."}
    base = dict(
        best=_camp("w", 10, 1.5), excluded_placements=[], best_device=None,
        best_age_range=None, suggested_budget=40.0, maturity=maturity,
        evaluated_outcomes=[_res("WIN", 2.0)],
    )
    con = build_analysis(report, **base, replicated_from_win=True)
    assert "replico la configuración exacta" in con["summary"]

    sin = build_analysis(report, **base, replicated_from_win=False)
    assert "replico la configuración exacta" not in sin["summary"]
