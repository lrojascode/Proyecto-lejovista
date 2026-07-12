"""M-2: reglas de campaña y umbrales de IA configurables por organización.

`Organization.campaign_rules_overrides` / `ai_thresholds_overrides` son JSON
vacíos por defecto — una org sin overrides debe comportarse exactamente
igual que antes (mismos defaults globales).
"""
from types import SimpleNamespace

from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES, get_campaign_rules_for_org
from app.services.ai.thresholds import DEFAULT_THRESHOLDS, get_thresholds_for_org


def test_get_campaign_rules_for_org_none_returns_global_defaults():
    assert get_campaign_rules_for_org(None) == TENANT_CAMPAIGN_RULES


def test_get_campaign_rules_for_org_empty_overrides_returns_global_defaults():
    org = SimpleNamespace(campaign_rules_overrides={})
    assert get_campaign_rules_for_org(org) == TENANT_CAMPAIGN_RULES


def test_get_campaign_rules_for_org_merges_partial_override():
    org = SimpleNamespace(campaign_rules_overrides={"objective": "OUTCOME_TRAFFIC"})
    rules = get_campaign_rules_for_org(org)
    assert rules["objective"] == "OUTCOME_TRAFFIC"
    # El resto de las claves sigue viniendo del default global, sin tocar.
    assert rules["optimization_goal"] == TENANT_CAMPAIGN_RULES["optimization_goal"]
    assert rules["default_status"] == TENANT_CAMPAIGN_RULES["default_status"]


def test_get_thresholds_for_org_none_returns_defaults():
    assert get_thresholds_for_org(None) is DEFAULT_THRESHOLDS


def test_get_thresholds_for_org_empty_overrides_returns_defaults():
    org = SimpleNamespace(ai_thresholds_overrides={})
    assert get_thresholds_for_org(org) is DEFAULT_THRESHOLDS


def test_get_thresholds_for_org_merges_partial_override():
    org = SimpleNamespace(ai_thresholds_overrides={"cpl_critical_multiplier": 3.0})
    t = get_thresholds_for_org(org)
    assert t.cpl_critical_multiplier == 3.0
    # El resto de los campos conserva el default (no es un objeto nuevo desde cero).
    assert t.frequency_saturation == DEFAULT_THRESHOLDS.frequency_saturation
    assert t.ctr_min_pct == DEFAULT_THRESHOLDS.ctr_min_pct


def test_get_thresholds_for_org_ignores_unknown_keys():
    """Un override con un campo viejo/typo no debe romper — se ignora."""
    org = SimpleNamespace(ai_thresholds_overrides={"campo_que_no_existe": 123})
    assert get_thresholds_for_org(org) is DEFAULT_THRESHOLDS
