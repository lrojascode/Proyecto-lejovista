from typing import Dict, Any, Optional

# Defaults globales — el segmento "MARCA" de la nomenclatura (§8 del
# playbook, ej. `ALT_MSJ_...`) SÍ es por-tenant ya hoy: se deriva de
# `AdAccount.name` (ver `_brand_code` en `app/workers/creation_tasks.py`), no
# de un literal hardcodeado como antes.

TENANT_CAMPAIGN_RULES: Dict[str, Any] = {
    # --- Geografía (execution/05 §2) ---
    # Dos formas, y la app tiene que entender las dos: hay clientes locales que
    # sí quieren Lima, y campañas LATAM que necesitan una lista de países.
    #
    # `geo_countries` gana cuando está presente. Vacío = se usa la región.
    # Códigos ISO-3166-1 alfa-2, que es lo que espera `geo_locations.countries`.
    "geo_countries": [],
    "default_geo_region_key": "2771", # Lima Region
    "allowed_geo_keys": ["2771"],
    "required_placements": ["facebook_reels"],
    # Posiciones de Instagram por defecto. Sustituye a la antigua
    # `banned_placements: ["instagram_positions"]`, que además de prohibir IG
    # era config MUERTA (declarada y nunca leída por nadie).
    "instagram_positions": ["stream", "story", "reels", "explore"],
    "default_status": "PAUSED",
    "forbidden_status": "ACTIVE",
    "objective": "OUTCOME_LEADS",
    "optimization_goal": "CONVERSATIONS",
    "destination_type": "WHATSAPP",
    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    "max_campaigns_per_window": 15,
    "campaign_window_hours": 24,

    # --- Segmentación por edad (execution/05 §3) ---
    # APAGADA por defecto, y es un cambio de criterio deliberado.
    #
    # El documento de ITS: "nunca segmentar por edad o género. La data es plana
    # (S/1.83 a S/2.68 entre extremos). Público amplio + Advantage+ y listo."
    #
    # Y hay una consecuencia técnica encima: si el rango ganador apunta a
    # público mayor, `build_targeting` se ve obligado a apagar
    # `advantage_audience` porque Meta no lo admite con `age_min > 25`. O sea
    # que segmentar por edad DESACTIVA justo lo que conviene usar.
    #
    # Queda como regla por organización, no como cambio global silencioso: una
    # org que hoy depende de su rango ganador puede volver a encenderla.
    "use_age_targeting": False,

    # --- Piso de presupuesto por conjunto (execution/05 §4) ---
    # "Presupuesto mínimo por conjunto: S/16/día. Al costo actual eso da los ~50
    # eventos/semana que Meta necesita. Por debajo, el conjunto nunca sale de
    # aprendizaje. Antes que crear dos conjuntos a S/8, creo uno a S/16."
    #
    # Es una regla operativa con fundamento (la fase de aprendizaje de Meta), no
    # una preferencia. AVISA, no bloquea: explicar el porqué es lo que convierte
    # un número en una decisión informada.
    "min_daily_budget_for_learning": 16.0,

    # --- Nomenclatura (execution/05 §7) ---
    # Token de TIPO del nombre de campaña. Estaba clavado en "MSJ" (mensajes),
    # coherente con que la app solo supiera hacer campañas de WhatsApp. El
    # documento de ITS usa un tipo que varía (LEADS / WA / TRAF), y el tipo ES
    # la plantilla de campaña.
    #
    # El resto del nombre ya se infiere del histórico de la propia cuenta
    # (ver app/services/campaign_naming.py); esto es solo el segmento de tipo.
    "campaign_name_type_token": "MSJ",
}


# Claves protegidas (Fase 6.6 / Roadmap 9): la activación automática por API
# está prohibida por seguridad de la cuenta del cliente; las campañas siempre
# se crean en estado PAUSED. Estas claves NO son overrideables por tenant.
IMMUTABLE_RULE_KEYS = {
    "default_status": "PAUSED",
    "forbidden_status": "ACTIVE",
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
        return dict(TENANT_CAMPAIGN_RULES)
    merged = {**TENANT_CAMPAIGN_RULES, **overrides}
    merged.update(IMMUTABLE_RULE_KEYS)
    return merged
