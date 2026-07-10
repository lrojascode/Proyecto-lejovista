from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
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
import os
import uuid
from fastapi import status
from celery import chain
from app.config import settings

from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.organization import Organization
from app.schemas.campaign_schemas import CampaignCreateRequest, CampaignRiskPreviewRequest
from app.schemas.schemas import CampaignDraftResponse
from app.services.campaign_validator import CampaignValidator
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_risk import assess_campaign_risk
from app.services.ai.llm import OpenRouterClient
from app.services.ai.token_tracking import UsageContext
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

# Nivel de riesgo base de la creación (playbook §2). Ya NO es el valor que se
# devuelve: el riesgo se calcula dinámicamente en `assess_campaign_risk`
# (Ajuste 1). Se conserva como piso conceptual de referencia.
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


@router.get("/suggestion")
async def get_campaign_suggestion(
    ad_account_id: str,
    product_name: str = "Producto",
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
        AdAccount.status != "ARCHIVED",
    )
    res_acc = await db.execute(q_acc)
    ad_account = res_acc.scalar_one_or_none()
    if not ad_account:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    org = await db.get(Organization, current_user.organization_id)

    suggestion = await CampaignSuggester(db).suggest(ad_account)
    copy = await OpenRouterClient().suggest_campaign_copy(
        org, ad_account, product_name=product_name,
        winning_context=suggestion.get("winning_campaign"),
        winning_ad_copy=suggestion.get("winning_ad_copy"),
        usage_ctx=UsageContext(
            feature="campaign_copy",
            organization_id=current_user.organization_id,
            ad_account_id=ad_account.id,
        ),
    )

    risk = assess_campaign_risk(
        budget=suggestion["suggested_budget"],
        copy_text=copy.get("primary_text", ""),
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
        "suggested_budget": suggestion["suggested_budget"],
        "suggested_days": suggestion["suggested_days"],
        "excluded_placements": suggestion["excluded_placements"],
        "best_device": suggestion["best_device"],
        "best_age_range": suggestion["best_age_range"],
        "winning_campaign": suggestion["winning_campaign"],
        "winning_ad_copy": suggestion["winning_ad_copy"],
        "evidence": suggestion["evidence"],
        "copy": copy,
    }


_ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB


@router.post("/upload-image")
async def upload_campaign_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Sube una foto para usar en la creación de campaña (reemplaza pegar una
    URL a mano). Se guarda en MEDIA_ROOT/{organization_id}/{uuid}.{ext} — el
    Saga de creación la lee del mismo disco vía `image_path`
    (MetaAssetRepository.get_or_upload_image), sin volver a subirla por HTTP."""
    ext = _ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa JPG, PNG o WebP.")

    contents = await file.read()
    if len(contents) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="La imagen supera el límite de 8MB.")

    org_dir = os.path.join(settings.MEDIA_ROOT, current_user.organization_id)
    os.makedirs(org_dir, exist_ok=True)
    filename = f"{uuid.uuid4()}{ext}"
    with open(os.path.join(org_dir, filename), "wb") as f:
        f.write(contents)

    return {"image_path": f"{current_user.organization_id}/{filename}"}


@router.post("/risk-preview")
async def preview_campaign_risk(
    request: CampaignRiskPreviewRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ajuste 1: calcula el nivel de riesgo con los valores ACTUALES del
    formulario (presupuesto/copy editados por el usuario) para mostrarlo ANTES
    de confirmar la creación. No escribe nada."""
    q_acc = select(AdAccount).where(
        AdAccount.id == request.ad_account_id,
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status != "ARCHIVED",
    )
    ad_account = (await db.execute(q_acc)).scalar_one_or_none()
    if not ad_account:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    # Sugerencia determinista (sin LLM) solo para tener `suggested_budget` /
    # `has_history` con qué comparar el presupuesto que puso el usuario.
    suggestion = await CampaignSuggester(db).suggest(ad_account)
    risk = assess_campaign_risk(
        budget=request.budget,
        copy_text=request.copy_text,
        suggestion=suggestion,
    )
    return {"risk_level": risk["level"], "risk_reasons": risk["reasons"]}


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
        AdAccount.organization_id == tenant_id,
        AdAccount.status != "ARCHIVED",
    )
    res_acc = await db.execute(q_acc)
    ad_account = res_acc.scalar_one_or_none()
    if not ad_account:
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

    validator = CampaignValidator(ad_account)

    # 1. Validación síncrona
    await validator.validate(request)

    # 1b. Razonamiento + riesgo dinámico (deterministas). La sugerencia se
    # recalcula del histórico real (sin LLM) para tener madurez/análisis con qué
    # documentar la decisión y comparar el presupuesto que envió el usuario.
    suggestion = await CampaignSuggester(db).suggest(ad_account)
    risk = assess_campaign_risk(
        budget=request.budget,
        copy_text=request.copy_text,
        suggestion=suggestion,
    )

    # 2. Registrar el intento (CampaignDraft real en BD — antes se generaba un
    # uuid suelto que no se persistía en ningún lado, así que el estado de la
    # Saga no se podía consultar de verdad).
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

    # 2b. Historial de entrenamiento (Ajuste 2A): guarda qué propuso la IA y por
    # qué, para que futuras creaciones aprendan de esto y detecten convergencia.
    learning_log = CampaignLearningLog(
        organization_id=tenant_id,
        ad_account_id=ad_account.id,
        draft_id=draft_id,
        product=request.product_name,
        params_json={
            "budget": request.budget,
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

    # 3. Preparar el contexto inicial
    initial_context = {
        "draft_id": draft_id,
        "tenant_id": tenant_id,
        "ad_account_id": request.ad_account_id,
        "page_id": request.page_id,
        "product_name": request.product_name,
        "budget": request.budget,
        "days": request.days,
        "whatsapp_number": request.whatsapp_number,
        "image_url": request.image_url,
        "image_hash": request.image_hash,
        "image_path": request.image_path,
        "video_id": request.video_id,
        "thumbnail_url": request.thumbnail_url,
        "copy_text": request.copy_text,
        # Señales de optimización derivadas del histórico ganador (Ajuste 2):
        # el AdSet las aplica en su `targeting` en vez de hardcodear edad/
        # dispositivo/placements. Fallback a defaults si no hay histórico.
        "best_age_range": suggestion.get("best_age_range"),
        "best_device": suggestion.get("best_device"),
        "excluded_placements": suggestion.get("excluded_placements") or [],
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
        "risk_level": risk["level"],
        "risk_reasons": risk["reasons"],
        "analysis": suggestion["analysis"],
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


@router.get("/learning-history")
async def get_learning_history(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db),
):
    """Historial de entrenamiento de la IA (Ajuste 2A): qué propuso y por qué en
    cada creación, con su madurez y riesgo. Scopeado a la cuenta activa."""
    q = select(CampaignLearningLog).where(
        CampaignLearningLog.organization_id == current_user.organization_id,
    )
    if ad_account_id:
        q = q.where(CampaignLearningLog.ad_account_id == ad_account_id)
    q = q.order_by(desc(CampaignLearningLog.created_at)).limit(max(1, min(limit, 100)))

    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": r.id,
            "product": r.product,
            "risk_level": r.risk_level,
            "status": r.status,
            "params": r.params_json,
            "analysis": r.analysis_json,
            "maturity": r.maturity_json,
            "outcome": r.outcome_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

