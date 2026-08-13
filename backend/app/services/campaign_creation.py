import datetime
import logging

from celery import chain
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.user import User
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.campaign_risk import assess_campaign_risk
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_validator import CampaignValidator
from app.workers.creation_tasks import (
    task_create_ad,
    task_create_adset,
    task_create_campaign,
    task_create_creative,
    task_handle_chain_error,
    task_upload_image,
)

logger = logging.getLogger(__name__)

# Anti-bloqueo (playbook §2): tope de campañas nuevas por ventana de tiempo,
# scopeado por organización (no hay un concepto explícito de "sesión"). Vive
# acá (no en el router) porque tanto `POST /campaigns/` como el chat de
# creación necesitan la misma cifra para el mismo chequeo.
MAX_CAMPAIGNS_PER_SESSION = 5
CAMPAIGN_SESSION_WINDOW_HOURS = 24


class CampaignCapReachedError(Exception):
    """Se alcanzó el tope de campañas nuevas de la ventana móvil (429 en HTTP,
    tarjeta de error en el chat — cada llamador decide cómo comunicarlo)."""

    def __init__(self, limit: int, window_hours: int):
        self.limit = limit
        self.window_hours = window_hours
        super().__init__(
            f"Se alcanzó el límite de {limit} campañas nuevas en las últimas {window_hours}h "
            "(protección anti-bloqueo de cuenta en Meta, ver playbook §2). Intenta de nuevo más tarde."
        )


async def count_recent_drafts(db: AsyncSession, organization_id: str) -> int:
    """Cuántos `CampaignDraft` se crearon en la ventana anti-bloqueo vigente.
    Expuesto aparte de `start_campaign_creation` para que el chat pueda avisar
    del tope ANTES de hacer preguntas, no solo al confirmar."""
    window_start = datetime.datetime.utcnow() - datetime.timedelta(hours=CAMPAIGN_SESSION_WINDOW_HOURS)
    q = select(func.count()).select_from(CampaignDraft).where(
        CampaignDraft.organization_id == organization_id,
        CampaignDraft.created_at >= window_start,
    )
    return (await db.execute(q)).scalar_one()


async def start_campaign_creation(
    db: AsyncSession,
    current_user: User,
    ad_account: AdAccount,
    request: CampaignCreateRequest,
) -> dict:
    """Valida, registra y despacha la Saga de creación de una campaña.

    Único camino de escritura real (regla A-1): tanto `POST /campaigns/` como
    el chat de creación llaman a esta función — ninguno de los dos construye su
    propio `CampaignDraft`/`CampaignLearningLog`/chain. Lanza
    `CampaignCapReachedError` o lo que lance `CampaignValidator.validate`
    (HTTPException); el llamador decide cómo mostrarlo.
    """
    tenant_id = current_user.organization_id

    recent_count = await count_recent_drafts(db, tenant_id)
    if recent_count >= MAX_CAMPAIGNS_PER_SESSION:
        raise CampaignCapReachedError(MAX_CAMPAIGNS_PER_SESSION, CAMPAIGN_SESSION_WINDOW_HOURS)

    validator = CampaignValidator(ad_account, db=db)
    await validator.validate(request)

    # Presupuesto normalizado a TOTAL. `budget` puede venir como monto diario
    # (budget_type="DAILY"); todo lo que razona sobre presupuesto —riesgo,
    # calibración R1 del suggester y el costo esperado del outcome loop
    # (`suggested_budget / 20`)— asume un total de la campaña.
    budget_total = request.total_budget()

    suggestion = await CampaignSuggester(db).suggest(ad_account, product_name=request.product_name)
    risk = assess_campaign_risk(
        budget=budget_total,
        copy_text=request.copy_text,
        suggestion=suggestion,
    )

    draft = CampaignDraft(
        organization_id=tenant_id,
        created_by_user_id=current_user.id,
        name=f"{request.product_name}",
        payload=request.model_dump(),
        status="PENDING_CREATION",
    )
    db.add(draft)
    await db.commit()
    draft_id = draft.id

    learning_log = CampaignLearningLog(
        organization_id=tenant_id,
        ad_account_id=ad_account.id,
        draft_id=draft_id,
        product=request.product_name,
        params_json={
            "budget": request.budget,
            "budget_type": request.budget_type,
            "budget_total": budget_total,
            "days": request.days,
            "objective": suggestion["objective"],
            "excluded_placements": suggestion["excluded_placements"],
            "best_device": suggestion["best_device"],
            "best_age_range": suggestion["best_age_range"],
            "suggested_budget": suggestion["suggested_budget"],
        },
        analysis_json=suggestion["analysis"],
        maturity_json=suggestion["maturity"],
        risk_level=risk["level"],
        status="CREATED",
    )
    db.add(learning_log)
    await db.commit()

    initial_context = {
        "draft_id": draft_id,
        "tenant_id": tenant_id,
        "ad_account_id": request.ad_account_id,
        "page_id": request.page_id,
        "product_name": request.product_name,
        "campaign_name": suggestion.get("suggested_campaign_name"),
        "budget": request.budget,
        "budget_type": request.budget_type,
        "days": request.days,
        "whatsapp_number": request.whatsapp_number,
        "image_url": request.image_url,
        "image_hash": request.image_hash,
        "image_path": request.image_path,
        "video_id": request.video_id,
        "video_path": request.video_path,
        "thumbnail_url": request.thumbnail_url,
        "copy_text": request.copy_text,
        "headline": request.headline,
        "description": request.description,
        "best_age_range": suggestion.get("best_age_range"),
        "best_device": suggestion.get("best_device"),
        "excluded_placements": suggestion.get("excluded_placements") or [],
    }

    creation_chain = chain(
        task_upload_image.s(initial_context),
        task_create_campaign.s().set(countdown=40),
        task_create_adset.s().set(countdown=40),
        task_create_creative.s().set(countdown=40),
        task_create_ad.s().set(countdown=40),
    ).on_error(task_handle_chain_error.s(draft_id, tenant_id))

    creation_chain.apply_async()

    return {
        "message": "Campaign creation started",
        "draft_id": draft_id,
        "risk_level": risk["level"],
        "risk_reasons": risk["reasons"],
        "analysis": suggestion["analysis"],
    }
