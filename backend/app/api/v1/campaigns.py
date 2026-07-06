from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from app.database import get_db
from app.models.user import User
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ad_account import AdAccount
from app.models.api_call_log import APICallLog
from app.schemas.schemas import CampaignSnapshotResponse
from app.utils.security import get_current_user, get_active_ad_account_id
from app.utils.throttle import enforce_cooldown
from app.workers.tasks import sync_ad_account
from typing import List, Optional
import datetime
from fastapi import status
from celery import chain

from app.config import settings
from app.models.campaign_draft import CampaignDraft
from app.models.organization import Organization
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.schemas.schemas import CampaignDraftResponse
from app.services.campaign_validator import CampaignValidator
from app.services.campaign_suggester import CampaignSuggester
from app.services.ai.llm import OpenRouterClient
from app.services.meta_api_client import MetaApiClient
from app.services.meta_credential_service import MetaCredentialService
from app.services.rate_limiter import RateLimiter
from app.services.audit_log_service import AuditLogService
from app.workers.creation_tasks import (
    task_upload_image,
    task_create_campaign,
    task_create_adset,
    task_create_creative,
    task_create_ad,
    task_handle_chain_error
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Anti-bloqueo (playbook §2): tope de campañas nuevas por ventana de tiempo,
# scopeado por organización (no hay un concepto explícito de "sesión").
MAX_CAMPAIGNS_PER_SESSION = 5
CAMPAIGN_SESSION_WINDOW_HOURS = 24

# Nivel de riesgo de la creación de campañas según el playbook (§2): toda
# escritura de creación (campaña/adset/creativo/ad en PAUSED) es riesgo medio
# — no hay riesgo alto porque nunca se activa por API.
CAMPAIGN_CREATION_RISK_LEVEL = "MEDIUM"

@router.get("/recent", response_model=List[CampaignSnapshotResponse])
async def list_recent_campaign_snapshots(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna la captura de métricas más reciente para cada campaña de la cuenta de
    anuncios activa (ver `get_active_ad_account_id`).
    """
    # Ventana amplia a propósito: "hoy" en el servidor (UTC) no es "hoy" en la
    # zona horaria de la cuenta de anuncios (ej. Lima, UTC-5), y Meta suele
    # tardar hasta 24-72h en terminar de procesar las métricas de un día. Con
    # una ventana de 2 días, ese desfase dejaba esta consulta en 0 resultados
    # justo después de medianoche UTC aunque el sync hubiera traído datos
    # recientes. 7 días da margen de sobra sin encarecer la consulta.
    cutoff_date = datetime.date.today() - datetime.timedelta(days=7)
    conditions = [
        CampaignSnapshot.organization_id == current_user.organization_id,
        CampaignSnapshot.date >= cutoff_date,
    ]
    if ad_account_id is not None:
        conditions.append(CampaignSnapshot.ad_account_id == ad_account_id)
    q = select(CampaignSnapshot).where(*conditions).order_by(
        CampaignSnapshot.campaign_id, desc(CampaignSnapshot.date)
    )

    res = await db.execute(q)
    all_snapshots = res.scalars().all()
    
    # Filtrar para obtener solo el más reciente por campaign_id
    seen_campaigns = set()
    latest_snapshots = []
    
    for s in all_snapshots:
        if s.campaign_id not in seen_campaigns:
            seen_campaigns.add(s.campaign_id)
            latest_snapshots.append(s)
            
    return latest_snapshots

@router.get("/api-call-history")
async def list_api_call_history(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Últimas llamadas a la API de Meta hechas por la cuenta activa (auditoría
    visible en el dashboard: cuándo se sincronizó, qué se pidió, si falló).
    """
    conditions = [AdAccount.organization_id == current_user.organization_id]
    if ad_account_id is not None:
        conditions.append(AdAccount.id == ad_account_id)
    q = (
        select(APICallLog, AdAccount.name)
        .join(AdAccount, APICallLog.ad_account_id == AdAccount.id)
        .where(*conditions)
        .order_by(desc(APICallLog.timestamp))
        .limit(min(limit, 50))
    )
    res = await db.execute(q)
    return [
        {
            "id": log.id,
            "ad_account_name": acc_name,
            "method": log.method,
            "endpoint": log.endpoint,
            "status_code": log.status_code,
            "timestamp": log.timestamp.isoformat(),
        }
        for log, acc_name in res.all()
    ]

@router.get("/{campaign_id}/history", response_model=List[CampaignSnapshotResponse])
async def get_campaign_history(
    campaign_id: str,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna el historial de snapshots diarios de una campaña específica para dibujar
    gráficos, acotado a la cuenta de anuncios activa (evita mezclar campañas con el
    mismo id entre dos cuentas de la misma organización).
    """
    conditions = [
        CampaignSnapshot.organization_id == current_user.organization_id,
        CampaignSnapshot.campaign_id == campaign_id,
    ]
    if ad_account_id is not None:
        conditions.append(CampaignSnapshot.ad_account_id == ad_account_id)
    q = select(CampaignSnapshot).where(*conditions).order_by(CampaignSnapshot.date.asc())

    res = await db.execute(q)
    return res.scalars().all()

@router.post("/sync-trigger")
async def trigger_manual_sync(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Dispara manualmente el worker de sincronización incremental para todas las cuentas del tenant.
    """
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status == "ACTIVE"
    )
    res = await db.execute(q)
    accounts = res.scalars().all()

    if not accounts:
        raise HTTPException(status_code=400, detail="No hay cuentas de anuncios activas conectadas.")

    # Cooldown: el sync golpea Meta (rate limit / posible baneo). Evitar spam.
    # Se consume solo si de verdad hay algo que sincronizar (ver hallazgo 3.1 del HANDOFF).
    await enforce_cooldown("sync", current_user.organization_id, cooldown_seconds=60)

    for acc in accounts:
        sync_ad_account.delay(acc.id, "INCREMENTAL")
        
    return {"message": f"Sincronización manual programada para {len(accounts)} cuentas de anuncios."}


def build_meta_client(tenant_id: str, db: AsyncSession) -> MetaApiClient:
    """Construye el cliente de Meta con dependencias reales (Redis de settings,
    tenant autenticado). Antes estaba hardcodeado a localhost + 'mock-tenant'."""
    import redis.asyncio as redis
    redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return MetaApiClient(
        tenant_id,
        MetaCredentialService(db),
        RateLimiter(redis_client),
        AuditLogService(db),
    )

@router.get("/suggestion")
async def get_campaign_suggestion(
    ad_account_id: str,
    product_name: str = "Producto",
    zone: str = "Lima",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Fase 3C del plan de creación de campañas: une la sugerencia estructural
    determinista (3A, `CampaignSuggester` — sin cifras inventadas, AI-1) con el
    copy generado por el LLM (3B, `OpenRouterClient.suggest_campaign_copy`) para
    pre-rellenar el formulario de "Crear campaña" con datos reales de la cuenta,
    en vez de que el cliente empiece desde cero.
    """
    q_acc = select(AdAccount).where(
        AdAccount.id == ad_account_id,
        AdAccount.organization_id == current_user.organization_id,
    )
    res_acc = await db.execute(q_acc)
    ad_account = res_acc.scalar_one_or_none()
    if not ad_account:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    org = await db.get(Organization, current_user.organization_id)

    suggestion = await CampaignSuggester(db).suggest(ad_account)
    copy = await OpenRouterClient().suggest_campaign_copy(
        org, ad_account, product_name=product_name, zone=zone,
        winning_context=suggestion.get("winning_campaign"),
    )

    return {
        "risk_level": CAMPAIGN_CREATION_RISK_LEVEL,
        "has_history": suggestion["has_history"],
        "objective": suggestion["objective"],
        "optimization_goal": suggestion["optimization_goal"],
        "destination_type": suggestion["destination_type"],
        "suggested_budget": suggestion["suggested_budget"],
        "suggested_days": suggestion["suggested_days"],
        "excluded_placements": suggestion["excluded_placements"],
        "best_device": suggestion["best_device"],
        "best_age_range": suggestion["best_age_range"],
        "winning_campaign": suggestion["winning_campaign"],
        "evidence": suggestion["evidence"],
        "copy": copy,
    }


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def create_campaign(
    request: CampaignCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Inicia la creación de una campaña en Meta Ads (Saga).
    """
    tenant_id = current_user.organization_id

    # 0. Validar que la cuenta destino pertenezca al tenant autenticado.
    q_acc = select(AdAccount).where(
        AdAccount.id == request.ad_account_id,
        AdAccount.organization_id == tenant_id
    )
    res_acc = await db.execute(q_acc)
    if not res_acc.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    # Anti-bloqueo (playbook §2: "Máx ~5 campañas nuevas por sesión"). Meta puede
    # inhabilitar la cuenta si detecta ráfagas de creación. No hay un concepto
    # explícito de "sesión" en el modelo, así que se aproxima con una ventana
    # móvil de 24h por organización.
    window_start = datetime.datetime.utcnow() - datetime.timedelta(hours=CAMPAIGN_SESSION_WINDOW_HOURS)
    q_recent = select(func.count()).select_from(CampaignDraft).where(
        CampaignDraft.organization_id == tenant_id,
        CampaignDraft.created_at >= window_start,
    )
    recent_count = (await db.execute(q_recent)).scalar_one()
    if recent_count >= MAX_CAMPAIGNS_PER_SESSION:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Se alcanzó el límite de {MAX_CAMPAIGNS_PER_SESSION} campañas nuevas en las "
                f"últimas {CAMPAIGN_SESSION_WINDOW_HOURS}h (protección anti-bloqueo de cuenta "
                "en Meta, ver playbook §2). Intenta de nuevo más tarde."
            ),
        )

    meta_client = build_meta_client(tenant_id, db)
    validator = CampaignValidator(tenant_id, meta_client)

    # 1. Validación síncrona
    await validator.validate(request)

    # 2. Registrar el intento (CampaignDraft real en BD — antes se generaba un
    # uuid suelto que no se persistía en ningún lado, así que el estado de la
    # Saga no se podía consultar de verdad).
    draft = CampaignDraft(
        organization_id=tenant_id,
        created_by_user_id=current_user.id,
        name=f"{request.product_name}_{request.zone}",
        payload=request.model_dump(),
        status="PENDING_CREATION",
    )
    db.add(draft)
    await db.commit()
    draft_id = draft.id

    # 3. Preparar el contexto inicial
    initial_context = {
        "draft_id": draft_id,
        "tenant_id": tenant_id,
        "ad_account_id": request.ad_account_id,
        "page_id": request.page_id,
        "product_name": request.product_name,
        "zone": request.zone,
        "budget": request.budget,
        "days": request.days,
        "whatsapp_number": request.whatsapp_number,
        "image_url": request.image_url,
        "image_hash": request.image_hash,
        "video_id": request.video_id,
        "thumbnail_url": request.thumbnail_url,
        "copy_text": request.copy_text
    }
    
    # 4. Despachar la Saga (Chain)
    # El chain ejecuta secuencialmente. set(countdown=40) asegura la pausa requerida por protocolo.
    creation_chain = chain(
        task_upload_image.s(initial_context),
        task_create_campaign.s().set(countdown=40),
        task_create_adset.s().set(countdown=40),
        task_create_creative.s().set(countdown=40),
        task_create_ad.s().set(countdown=40)
    ).on_error(task_handle_chain_error.s(draft_id, tenant_id))
    
    creation_chain.apply_async()

    return {
        "message": "Campaign creation started",
        "draft_id": draft_id,
        "risk_level": CAMPAIGN_CREATION_RISK_LEVEL,
    }


@router.get("/drafts/{draft_id}", response_model=CampaignDraftResponse)
async def get_campaign_draft_status(
    draft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Estado real de una Saga de creación de campaña (antes el frontend solo
    simulaba el polling con un setInterval falso que siempre terminaba en éxito).
    `payload["result"]` se va llenando con los IDs de Meta conforme avanza cada
    paso del chain (campaign_id, adset_id, creative_id, ad_id).
    """
    draft = await db.get(CampaignDraft, draft_id)
    if not draft or draft.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Draft de campaña no encontrado.")
    return draft

