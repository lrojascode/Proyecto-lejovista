from typing import Dict, Any, Optional

# Defaults globales — el segmento "MARCA" de la nomenclatura (§8 del
# playbook, ej. `ALT_MSJ_...`) SÍ es por-tenant ya hoy: se deriva de
# `AdAccount.name` (ver `_brand_code` en `app/workers/creation_tasks.py`), no
# de un literal hardcodeado como antes.

TENANT_CAMPAIGN_RULES: Dict[str, Any] = {
    "default_geo_region_key": "2771", # Lima Region
    "allowed_geo_keys": ["2771"],
    "required_placements": ["facebook_reels"],
    "banned_placements": ["instagram_positions"],
    "default_status": "PAUSED",
    "forbidden_status": "ACTIVE",
    "objective": "OUTCOME_LEADS",
    "optimization_goal": "CONVERSATIONS",
    "destination_type": "WHATSAPP",
    "bid_strategy": "LOWEST_COST_WITHOUT_CAP"
}


def get_campaign_rules_for_org(organization: Optional[object] = None) -> Dict[str, Any]:
    """M-2: reglas de campaña aplicables a una organización.

    Lee `Organization.campaign_rules_overrides` (JSON, vacío por defecto) y
    superpone solo las claves presentes sobre `TENANT_CAMPAIGN_RULES` — una
    org sin overrides configurados obtiene exactamente el dict global de
    siempre (mismo objeto, mismo comportamiento).
    """
    overrides = getattr(organization, "campaign_rules_overrides", None) or {}
    if not overrides:
        return TENANT_CAMPAIGN_RULES
    return {**TENANT_CAMPAIGN_RULES, **overrides}
