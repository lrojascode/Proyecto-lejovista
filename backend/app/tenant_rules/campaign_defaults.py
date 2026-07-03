from typing import Dict, Any

# TODO: Refactorizar estas reglas a una tabla de base de datos (TenantCampaignRules)
# para que cada Inquilino (Tenant) pueda tener sus propias reglas configurables.
# Para la v1.0, las mantenemos como constantes centralizadas.

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
