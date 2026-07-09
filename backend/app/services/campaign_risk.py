"""Evaluación de riesgo de crear una campaña (Ajuste 1).

Antes el nivel de riesgo era una constante fija ("MEDIUM") y solo se mostraba
DESPUÉS de apretar "Crear". Ahora se calcula de forma dinámica a partir del
presupuesto propuesto vs. lo que sugiere el histórico, del estado de la cuenta y
del copy, y se puede consultar ANTES de confirmar (endpoint /risk-preview) para
que el usuario decida informado.

Regla base del playbook (§2): crear siempre es al menos MEDIUM porque es una
escritura real a Meta; nunca es "sin riesgo". Nunca es CRITICAL porque la
campaña se crea PAUSADA (no gasta hasta activarse a mano).
"""
from typing import Any, Dict, List, Optional

# Términos que Meta suele rechazar en anuncios (rubro farmacéutico/salud). Mismo
# criterio que el system prompt del copywriter en llm.py — aquí se usa para
# advertir de riesgo de rechazo antes de crear.
_POLICY_FLAGGED_TERMS = [
    "medicamento", "farmac", "dosis", "receta medica", "receta médica",
    "cura ", "curar", "tratamiento medico", "tratamiento médico",
    "adelgaz", "bajar de peso", "milagro", "garantizado 100",
]

# Umbral absoluto (moneda local) por encima del cual un presupuesto se considera
# alto aunque no haya histórico con qué compararlo.
_HIGH_ABSOLUTE_BUDGET = 500.0


def assess_campaign_risk(
    *,
    budget: float,
    copy_text: str = "",
    suggestion: Optional[Dict[str, Any]] = None,
    account_status: Optional[int] = None,
) -> Dict[str, Any]:
    """Devuelve {'level': 'LOW'|'MEDIUM'|'HIGH', 'reasons': [...]}.

    `suggestion` es el dict de `CampaignSuggester.suggest` (opcional): si viene,
    se compara el presupuesto pedido contra `suggested_budget` y se considera si
    la cuenta tiene histórico ganador.
    """
    reasons: List[str] = []
    escalate_high = False
    can_be_low = True

    suggested_budget = (suggestion or {}).get("suggested_budget")
    has_history = bool((suggestion or {}).get("has_history"))

    # 1. Presupuesto vs. lo sugerido por el histórico real.
    if suggested_budget and suggested_budget > 0:
        ratio = budget / suggested_budget
        if ratio > 2.0:
            escalate_high = True
            reasons.append(
                f"El presupuesto (S/.{budget:.0f}) es más del doble de lo que sugiere el "
                f"histórico de la cuenta (S/.{suggested_budget:.0f}). Riesgo de sobregasto."
            )
        elif ratio > 1.2:
            can_be_low = False
            reasons.append(
                f"El presupuesto (S/.{budget:.0f}) supera lo sugerido (S/.{suggested_budget:.0f}) "
                "en más del 20%."
            )
    else:
        # Sin histórico con conversiones: no hay evidencia para calibrar el gasto.
        can_be_low = False
        reasons.append(
            "La cuenta aún no tiene histórico con conversiones; el presupuesto no se puede "
            "calibrar contra datos reales."
        )

    if budget >= _HIGH_ABSOLUTE_BUDGET:
        escalate_high = True
        reasons.append(f"Presupuesto alto en términos absolutos (S/.{budget:.0f}).")

    # 2. Copy con lenguaje que Meta suele rechazar.
    lowered = (copy_text or "").lower()
    flagged = sorted({t.strip() for t in _POLICY_FLAGGED_TERMS if t in lowered})
    if flagged:
        escalate_high = True
        reasons.append(
            "El copy contiene términos que Meta suele rechazar (posible desaprobación del "
            f"anuncio): {', '.join(flagged)}."
        )

    # 3. Estado de la cuenta (1 = activa). Cualquier otro estado es más riesgoso.
    if account_status is not None and account_status != 1:
        escalate_high = True
        reasons.append(f"La cuenta de anuncios no está activa (estado {account_status}).")

    # 4. Placements a excluir detectados pero informativos (no cambian el nivel).
    excluded = (suggestion or {}).get("excluded_placements") or []
    if excluded:
        reasons.append(
            "Hay placements con gasto y 0 conversiones que conviene excluir: "
            + ", ".join(p.replace("_", " ") for p in excluded) + "."
        )

    if escalate_high:
        level = "HIGH"
    elif can_be_low and has_history:
        level = "LOW"
        if not reasons:
            reasons.append(
                "Presupuesto dentro del rango sugerido por el histórico ganador de la cuenta "
                "y copy sin términos problemáticos."
            )
    else:
        level = "MEDIUM"

    # Recordatorio constante: siempre se crea PAUSADA.
    reasons.append("La campaña se crea PAUSADA: no gasta hasta que la actives manualmente en Meta.")

    return {"level": level, "reasons": reasons}
