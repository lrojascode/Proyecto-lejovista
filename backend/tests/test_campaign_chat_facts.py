import pytest
from app.models.ad_account import AdAccount
from app.services.campaign_chat.facts import resolve_facts, PLAN_BY_TOPIC
from app.services.campaign_chat.grounding import GroundingSet


def _acc():
    acc = AdAccount(id="acc_1", name="Cuenta Test", spend_cap=5000.0, amount_spent=2000.0, status="ACTIVE")
    return acc


@pytest.mark.asyncio
async def test_resolve_facts_kpis_and_calculator(mocker):
    mocker.patch(
        "app.services.campaign_chat.facts.days_of_history",
        return_value=30,
    )
    mocker.patch(
        "app.services.campaign_chat.facts.get_kpis_with_deltas",
        return_value={
            "current": {"spend": 1200.0, "conversations": 40, "cost_per_conv": 30.0},
            "previous": {"spend": 1000.0, "conversations": 35, "cost_per_conv": 28.5},
            "deltas_pct": {"spend": 20.0, "conversations": 14.3, "cost_per_conv": 5.3},
        },
    )
    mocker.patch("app.services.campaign_chat.facts.get_report", return_value={})

    from app.services.ai.llm import ChatPlan
    plan = ChatPlan(topic="SPEND", blocks=["kpis", "account"])
    facts, canonical = await resolve_facts(None, "org_1", _acc(), plan, window_days=30)
    
    # Verificaciones de bloques
    assert "kpis_del_periodo" in facts
    assert facts["kpis_del_periodo"]["spend"] == 1200.0
    assert facts["kpis_del_periodo"]["gasto_promedio_diario"] == 40.0  # 1200 / 30
    assert facts["kpis_del_periodo"]["proyeccion_mensual_gasto"] == 1200.0
    
    assert "account" in facts
    assert facts["account"]["spend_cap"] == 5000.0
    assert facts["account"]["amount_spent"] == 2000.0
    assert facts["account"]["tope_restante"] == 3000.0
    assert facts["account"]["dias_para_agotar_tope"] == 75.0  # 3000 / 40.0

    # Texto canónico (formato local: separador de miles con punto, decimales con coma)
    assert "S/. 1,200.00" in canonical
    assert "+20.0%" in canonical
    assert "S/. 3,000.00" in canonical


@pytest.mark.asyncio
async def test_resolve_facts_grounding_invariant(mocker):
    mocker.patch("app.services.campaign_chat.facts.days_of_history", return_value=30)
    mocker.patch("app.services.campaign_chat.facts.get_kpis_with_deltas", return_value={
        "current": {"spend": 500.0, "conversations": 25, "cost_per_conv": 20.0},
        "previous": {"spend": 400.0, "conversations": 20, "cost_per_conv": 20.0},
        "deltas_pct": {"spend": 25.0, "conversations": 25.0, "cost_per_conv": 0.0},
    })
    mocker.patch("app.services.campaign_chat.facts.get_report", return_value={
        "campaigns": [{"name": f"Camp {i}", "spend": i * 10, "conversations": i, "cost_per_conv": 10.0, "ctr": 1.5} for i in range(20)],
        "placements_summary": {"facebook_feed": {"spend": 200.0}},
        "demographics_summary": {"25-34": {"spend": 150.0}},
        "insights": [{"level": "WARNING", "category": "PERFORMANCE", "title": "CTR bajo"}],
        "recommendations": ["Pausar anuncios con fatiga"],
    })

    facts, _ = await resolve_facts(None, "org_1", _acc(), ["kpis", "ranking", "breakdowns", "alerts", "recommendations"])
    gset = GroundingSet.from_data(facts)
    assert gset.size() <= 220


@pytest.mark.asyncio
async def test_resolve_facts_ranking_filters_zero_conversations_and_reads_cost_per_conv(mocker):
    mocker.patch("app.services.campaign_chat.facts.days_of_history", return_value=30)
    mocker.patch("app.services.campaign_chat.facts.get_kpis_with_deltas", return_value={
        "current": {"spend": 500.0, "conversations": 25, "cost_per_conv": 20.0},
        "previous": {"spend": 400.0, "conversations": 20, "cost_per_conv": 20.0},
        "deltas_pct": {"spend": 25.0, "conversations": 25.0, "cost_per_conv": 0.0},
    })
    # Campaña muerta (spend=0, conv=0, cost_per_conv=0.0) vs campaña activa con resultados
    mocker.patch("app.services.campaign_chat.facts.get_report", return_value={
        "campaigns": [
            {"name": "Camp Muerta", "status": "PAUSED", "spend": 0.0, "conversations": 0, "cost_per_conv": 0.0, "ctr": 0.0},
            {"name": "Camp Ganadora", "status": "ACTIVE", "spend": 250.0, "conversations": 20, "cost_per_conv": 12.5, "ctr": 2.1},
            {"name": "Camp Regular", "status": "ACTIVE", "spend": 150.0, "conversations": 5, "cost_per_conv": 30.0, "ctr": 1.2},
        ],
    })

    facts, canonical = await resolve_facts(None, "org_1", _acc(), "BEST_CAMPAIGN")
    ranking_camps = facts.get("campaigns", [])
    
    # Camp Muerta (0 conv) no debe estar en el ranking
    assert len(ranking_camps) == 2
    assert ranking_camps[0]["name"] == "Camp Ganadora"
    assert ranking_camps[0]["cost_per_conv"] == 12.5
    assert ranking_camps[0]["conversations"] == 20
    assert "S/. 0.00" not in canonical
    assert "Camp Ganadora" in canonical
    assert "S/. 12.50" in canonical


@pytest.mark.asyncio
async def test_resolve_facts_with_none_topic_in_chat_plan_does_not_crash(mocker):
    """Punto 4 de fase 06: ChatPlan(topic=None) no debe lanzar TypeError: 'NoneType' object is not iterable."""
    mocker.patch("app.services.campaign_chat.facts.days_of_history", return_value=30)
    mocker.patch("app.services.campaign_chat.facts.get_kpis_with_deltas", return_value={
        "current": {"spend": 100.0, "conversations": 5, "cost_per_conv": 20.0},
        "previous": {"spend": 100.0, "conversations": 5, "cost_per_conv": 20.0},
        "deltas_pct": {"spend": 0.0, "conversations": 0.0, "cost_per_conv": 0.0},
    })
    from app.services.ai.llm import ChatPlan
    plan = ChatPlan(blocks=["kpis"], topic=None)
    facts, canonical = await resolve_facts(None, "org_1", _acc(), plan)
    assert "kpis_del_periodo" in facts
    assert isinstance(canonical, str)
    assert len(canonical) > 0
