import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.user import User
from app.services.ai.llm import OpenRouterClient
from app.services.ai.token_tracking import UsageContext
from app.services.campaign_risk import assess_campaign_risk
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_validator import normalize_product_name
from app.services.recuperacion import recuperar_comparables


async def build_campaign_proposal(
    db: AsyncSession,
    current_user: User,
    ad_account: AdAccount,
    product_name: str,
    image_path: Optional[str] = None,
) -> dict:
    """Une la sugerencia estructural determinista (`CampaignSuggester`, sin
    cifras inventadas — AI-1) con el copy generado por el LLM (con visión si
    se pasa `image_path`) y el riesgo. Único lugar que arma este objeto: lo
    usan `GET /campaigns/suggestion` (formulario) y el chat de creación (CH-1),
    para que ambas puertas de entrada muestren exactamente los mismos números.

    Valida que `image_path` pertenezca a la organización del usuario — es una
    ruta que llega del cliente y termina leyendo del disco.
    """
    if image_path and not image_path.startswith(f"{current_user.organization_id}/"):
        raise HTTPException(status_code=400, detail="La imagen no pertenece a esta organización.")

    # El nombre de campaña que se arma acá se le muestra al usuario como si ya
    # fuera válido. Sin esto, "diseño web a medida" produce
    # AGENCIAKIB_MSJ_DISEÑOWEBAMEDIDA_AGO2026 y recién al confirmar salta el
    # error de `PRODUCT_NAME_PATTERN` — la sugerencia llevaba al usuario a un
    # callejón sin salida (reportado 14-ago-2026).
    product_name = normalize_product_name(product_name)

    org = await db.get(Organization, current_user.organization_id)

    suggestion = await CampaignSuggester(db).suggest(ad_account, product_name=product_name)
    copy = await OpenRouterClient(organization=org).suggest_campaign_copy(
        org, ad_account, product_name=product_name,
        winning_context=suggestion.get("winning_campaign"),
        winning_ad_copy=suggestion.get("winning_ad_copy"),
        image_path=image_path,
        diagnostics=suggestion.get("analysis"),
        usage_ctx=UsageContext(
            feature="campaign_copy",
            organization_id=current_user.organization_id,
            ad_account_id=ad_account.id,
        ),
    )

    # Campañas históricas comparables de ESTA cuenta (spec 11). Se recupera
    # después de la sugerencia porque la referencia se arma con lo que la
    # sugerencia decidió: objetivo, evento de conversión y presupuesto.
    #
    # Es una lectura, no una escritura: la trazabilidad se persiste cuando la
    # propuesta se materializa (`start_campaign_creation`), no cada vez que
    # alguien abre el formulario.
    evidencia_comparable = await recuperar_comparables(
        db, ad_account,
        referencia={
            "campaign_id": None,
            "objetivo": suggestion["objective"],
            "optimization_goal": suggestion["optimization_goal"],
            "gasto": suggestion["suggested_budget"],
            "fecha": datetime.date.today(),
        },
    )

    risk = assess_campaign_risk(
        budget=suggestion["suggested_budget"],
        # 3.4: primary_text ahora es un array de 2 variantes; se evalúa la
        # primera para el chequeo de riesgo (lenguaje que Meta suele rechazar).
        copy_text=(copy.get("primary_text") or [""])[0],
        suggestion=suggestion,
    )

    return {
        "risk_level": risk["level"],
        "risk_reasons": risk["reasons"],
        "has_history": suggestion["has_history"],
        "maturity": suggestion["maturity"],
        "analysis": suggestion["analysis"],
        "objective": suggestion["objective"],
        "optimization_goal": suggestion["optimization_goal"],
        "destination_type": suggestion["destination_type"],
        "suggested_campaign_name": suggestion.get("suggested_campaign_name"),
        "suggested_budget": suggestion["suggested_budget"],
        "suggested_days": suggestion["suggested_days"],
        "excluded_placements": suggestion["excluded_placements"],
        "best_device": suggestion["best_device"],
        "best_age_range": suggestion["best_age_range"],
        "winning_campaign": suggestion["winning_campaign"],
        "winning_ad_copy": suggestion["winning_ad_copy"],
        "evidence": suggestion["evidence"],
        "copy": copy,
        "suggested_product_name": copy.get("suggested_product_name"),
        # La evidencia que respalda esta propuesta, o la marca de exploratoria
        # cuando no hay ninguna (spec 11, criterios 1, 5, 7 y 8).
        "evidencia": evidencia_comparable.as_dict(),
    }
