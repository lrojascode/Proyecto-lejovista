import datetime
import logging

from celery import chain
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.user import User
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.ai.llm import CAMPAIGN_COPY_PROMPT_VERSION, OpenRouterClient
from app.models.operacion import CAP_CREACION
from app.services import interruptores
from app.services.baseline_repository import baseline_de_la_cuenta
from app.services.recuperacion import persistir_evidencia, recuperar_comparables
from app.services.budget_gate import assert_cabe_en_el_mes
from app.services.proposal_versioning import (
    aprobar, consumir_aprobacion, crear_version,
)
from app.services.campaign_risk import assess_campaign_risk
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_validator import CampaignValidator, normalize_product_name
from app.services.asset_scope import page_ids_of_accounts
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

    # Interruptor operativo de creación (spec 13). Va acá y no en el endpoint
    # porque este es el único camino de escritura: el formulario y el chat pasan
    # los dos por aquí, y ponerlo en uno solo dejaría el otro abierto.
    await interruptores.assert_encendida(
        db, CAP_CREACION, organization_id=tenant_id,
    )

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

    await _rechazar_pagina_ajena(db, tenant_id, ad_account, request.page_id)

    # Presupuesto normalizado a TOTAL. `budget` puede venir como monto diario
    # (budget_type="DAILY"); todo lo que razona sobre presupuesto —riesgo,
    # calibración R1 del suggester y el costo esperado del outcome loop
    # (`suggested_budget / 20`)— asume un total de la campaña.
    budget_total = request.total_budget()

    # Compuerta de presupuesto mensual (spec 02). Va DESPUÉS de validar y ANTES
    # de persistir el Draft: un Draft en PENDING_CREATION ya compromete margen,
    # así que crearlo para después rechazar contaminaría el cálculo del mes.
    #
    # Está acá y no en el endpoint porque este es el único camino de creación
    # real (A-1): lo comparten `POST /campaigns/` y el chat de creación. Puesta
    # en el endpoint, el chat la evitaría.
    await assert_cabe_en_el_mes(db, tenant_id, budget_total)

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
    await db.flush()
    draft_id = draft.id

    # Versión inmutable + autorización de un solo uso (spec 03). En este camino
    # el operador que llama ES quien autoriza: la versión se congela, se aprueba
    # y se consume en la misma transacción. En el camino de revisión (crear
    # versión → aprobar → ejecutar) los tres pasos están separados, pero los
    # objetos son los mismos, así que la Saga no distingue de dónde viene la
    # autorización que le llega.
    # Baseline CONGELADO (spec 06): el número contra el que se prometió esta
    # campaña, y contra el que se la evaluará toda su vida. El baseline vigente
    # de la cuenta seguirá recalibrándose con temporada y variantes; los dos
    # coexisten sin pisarse. Reevaluar el pasado con el baseline de hoy destruye
    # la posibilidad de saber si el sistema acertó.
    baseline_congelado = await baseline_de_la_cuenta(db, ad_account.id)

    version = await crear_version(
        db,
        draft_id=draft_id,
        organization_id=tenant_id,
        ad_account_id=ad_account.id,
        config=request.model_dump(),
        risk_level=risk["level"],
        gates={
            "budget_total": float(budget_total),
            "risk_reasons": risk["reasons"],
            "preflight": "ok",
            "baseline_congelado": baseline_congelado.as_dict(),
        },
        created_by_user_id=current_user.id,
    )
    aprobacion = await aprobar(db, version, user_id=current_user.id)
    await db.commit()

    aprobacion = await consumir_aprobacion(db, aprobacion.id)
    await db.commit()

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

    # Trazabilidad de la propuesta (spec 11, criterio 10). Se escribe junto al
    # registro de aprendizaje y ANTES de lanzar la Saga: si se escribiera
    # después, una creación que falla dejaría una campaña sin poder responder en
    # qué se basó — y esas son justamente las que hay que revisar.
    #
    # El baseline va CONGELADO, el mismo que se metió en los gates de la
    # versión. El outcome loop lo lee de acá para juzgar contra la referencia
    # que estaba vigente al decidir, no contra la de hoy (criterio 13).
    try:
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
        # `org` y `rules` solo estaban ligadas dentro de la rama del tope de
        # campañas: se resuelven acá para este bloque.
        org_actual = await db.get(Organization, tenant_id)
        reglas_actuales = get_campaign_rules_for_org(org_actual)
        cliente_ia = OpenRouterClient(organization=org_actual)
        await persistir_evidencia(
            db,
            organization_id=tenant_id,
            ad_account_id=ad_account.id,
            evidencia=evidencia_comparable,
            baseline_congelado=baseline_congelado.as_dict(),
            draft_id=draft_id,
            version_id=version.id,
            reglas_version=str((reglas_actuales or {}).get("version") or "tenant-rules-v1"),
            prompt_version=CAMPAIGN_COPY_PROMPT_VERSION,
            modelo=org_actual.ai_model if org_actual else None,
            proveedor=cliente_ia.provider,
        )
    except Exception:
        # Canal lateral: la trazabilidad no puede impedir que se cree una
        # campaña ya aprobada y autorizada. Se registra el fallo, que es lo que
        # permite detectar que se está perdiendo.
        logger.exception(
            "No se pudo persistir la trazabilidad de la propuesta (draft=%s). "
            "La campaña sigue; el registro de en qué se basó se pierde.",
            draft_id,
        )

    await db.commit()

    initial_context = {
        "draft_id": draft_id,
        # La clave de idempotencia de cada paso deriva de esto: sin la
        # autorización en el contexto, un reintento de la Saga no puede saber
        # que ya escribió.
        "approval_id": aprobacion.id,
        "version_id": version.id,
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


async def _rechazar_pagina_ajena(
    db: AsyncSession, tenant_id: str, ad_account: AdAccount, page_id
) -> None:
    """Que no se publique desde la página de otro cliente.

    `page_id` llega en el cuerpo de la request. Antes no se validaba contra
    nada: el único freno era que el token de Meta pudiera promocionar esa
    página — y en una agencia cuyo token alcanza a varios clientes, eso no
    frena nada.

    Se rechaza solo cuando la página EXISTE en el catálogo de la organización y
    NO la alcanza esta cuenta, que es exactamente el caso del cruce. Una página
    que no está en el catálogo se deja pasar: el descubrimiento tiene huecos
    conocidos (el caso "Althea pack", una página que Ads Manager ofrecía
    promocionar y no figuraba en ninguna fuente directa), y bloquearla acá
    convertiría ese hueco en una campaña que no se puede crear.
    """
    from app.models.meta_page import MetaPage

    if not page_id:
        return

    conocida = (await db.execute(
        select(MetaPage.id).where(
            MetaPage.organization_id == tenant_id,
            MetaPage.external_page_id == str(page_id),
        )
    )).scalar_one_or_none()
    if not conocida:
        return

    alcanzable = (await db.execute(
        select(MetaPage.id).where(
            MetaPage.id == conocida,
            MetaPage.id.in_(page_ids_of_accounts([ad_account.id])),
        )
    )).scalar_one_or_none()
    if not alcanzable:
        logger.warning(
            "Se intentó crear una campaña en la cuenta %s con la página %s, que no "
            "corresponde a esa cuenta.", ad_account.id, page_id,
        )
        raise HTTPException(
            status_code=400,
            detail="Esa página de Facebook no corresponde a la cuenta publicitaria seleccionada.",
        )
