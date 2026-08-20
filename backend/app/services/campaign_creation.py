import datetime
import logging

from celery import chain
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.user import User
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.campaign_risk import assess_campaign_risk
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_validator import CampaignValidator, normalize_product_name
from app.workers.creation_tasks import (
    task_create_ad,
    task_create_adset,
    task_create_campaign,
    task_create_creative,
    task_handle_chain_error,
    task_upload_image,
    task_verify_campaign,
)

from app.models.organization import Organization
from app.tenant_rules.campaign_defaults import get_campaign_rules_for_org

logger = logging.getLogger(__name__)

# Anti-bloqueo (playbook §2): tope de campañas nuevas por ventana de tiempo,
# configurable por organización vía overrides de reglas (default: 15 / 24h).
DEFAULT_MAX_CAMPAIGNS_PER_WINDOW = 15
DEFAULT_CAMPAIGN_WINDOW_HOURS = 24
MAX_CAMPAIGNS_PER_SESSION = DEFAULT_MAX_CAMPAIGNS_PER_WINDOW
CAMPAIGN_SESSION_WINDOW_HOURS = DEFAULT_CAMPAIGN_WINDOW_HOURS


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


# Prefijo de los ids que genera la Saga cuando las escrituras están simuladas
# (ver `creation_tasks`). Es la prueba retroactiva de que un draft nunca salió
# del servidor, incluso en las filas anteriores a que existiera `simulated`.
MOCK_ID_PREFIX = "mock_"


def _draft_reached_meta(draft: CampaignDraft) -> bool:
    """¿Este draft llegó de verdad a Meta?

    Dos señales, porque la marca explícita solo existe en los drafts creados
    después de que se agregara: un `campaign_id` con prefijo `mock_` delata una
    simulación igual de bien y funciona hacia atrás, sin tener que tocar la BD.

    Ante la ausencia de las dos —draft en vuelo, o que falló antes de llegar a
    crear la campaña— se cuenta: el tope protege la cuenta de Meta y equivocarse
    para el lado de contarlo de más es el error barato.
    """
    result = (draft.payload or {}).get("result") or {}
    if result.get("simulated", False):
        return False
    campaign_id = result.get("campaign_id") or ""
    return not campaign_id.startswith(MOCK_ID_PREFIX)


async def count_recent_drafts(db: AsyncSession, organization_id: str, window_hours: int = 24) -> int:
    """Cuántas campañas que REALMENTE llegaron a Meta se crearon en la ventana
    anti-bloqueo vigente.

    Las simuladas no cuentan (ver `_draft_reached_meta`): el tope existe para
    que Meta no bloquee la cuenta por crear demasiadas campañas seguidas, y una
    campaña simulada no hace ni una sola llamada a Meta. Contarlas dejaba el
    sistema trabado 24h por intentos que nunca salieron del servidor — y encima
    el usuario veía "ya creaste 5" habiendo creado cero (reportado
    14-ago-2026).

    Se filtra en Python y no en SQL porque `payload` es JSON y la sintaxis para
    entrar a una clave anidada difiere entre Postgres y el SQLite de los tests;
    la ventana es de 24h por organización, así que son unas pocas filas.
    """
    window_start = datetime.datetime.utcnow() - datetime.timedelta(hours=window_hours)
    q = select(CampaignDraft).where(
        CampaignDraft.organization_id == organization_id,
        CampaignDraft.created_at >= window_start,
    )
    drafts = (await db.execute(q)).scalars().all()
    return sum(1 for d in drafts if _draft_reached_meta(d))


async def cap_reached(db: AsyncSession, organization_id: str, ad_account: AdAccount) -> bool:
    """¿Hay que frenar por el tope anti-bloqueo?

    Único lugar donde se decide: lo consultan `start_campaign_creation` y las
    dos compuertas del chat (al abrir la sesión y al confirmar la creación).
    Cuando estaban repartidas, cada una podía quedar con un criterio distinto.

    Con las escrituras simuladas devuelve `False` siempre: el tope existe para
    que Meta no bloquee la cuenta, y una campaña simulada no le manda ni una
    llamada.
    """
    if settings.simulate_meta_writes_for(ad_account.id, ad_account.external_account_id):
        return False
    org = await db.get(Organization, organization_id)
    rules = get_campaign_rules_for_org(org)
    max_limit = rules.get("max_campaigns_per_window", DEFAULT_MAX_CAMPAIGNS_PER_WINDOW)
    window_hours = rules.get("campaign_window_hours", DEFAULT_CAMPAIGN_WINDOW_HOURS)
    return await count_recent_drafts(db, organization_id, window_hours=window_hours) >= max_limit


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

    if await cap_reached(db, tenant_id, ad_account):
        org = await db.get(Organization, tenant_id)
        rules = get_campaign_rules_for_org(org)
        max_limit = rules.get("max_campaigns_per_window", DEFAULT_MAX_CAMPAIGNS_PER_WINDOW)
        window_hours = rules.get("campaign_window_hours", DEFAULT_CAMPAIGN_WINDOW_HOURS)
        raise CampaignCapReachedError(max_limit, window_hours)

    # Se normaliza ANTES de validar: la regex de `product_name` no admite
    # tildes ni ñ, y hacer que un peruano escriba "diseno" en un campo que dice
    # "Producto" no es una regla de negocio, es una fuga de un detalle técnico
    # de Meta. El chat ya normalizaba por su cuenta (`sanitize_product_name`);
    # esto cierra el mismo hueco para el formulario, que era el único camino
    # donde el error llegaba al usuario.
    request.product_name = normalize_product_name(request.product_name)

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
        # Paso 6: releer los cuatro niveles en Meta. Hasta ahora "creada con
        # exito" solo significaba "Meta acepto el POST", y Meta acepta la
        # creacion y despues marca el objeto con problemas de politica o de
        # configuracion en `issues_info`. Sin releer, el usuario ve un cartel de
        # exito sobre una campana que no va a entregar nunca.
        task_verify_campaign.s().set(countdown=40),
    ).on_error(task_handle_chain_error.s(draft_id, tenant_id))

    creation_chain.apply_async()

    return {
        "message": "Campaign creation started",
        "draft_id": draft_id,
        "risk_level": risk["level"],
        "risk_reasons": risk["reasons"],
        "analysis": suggestion["analysis"],
    }
