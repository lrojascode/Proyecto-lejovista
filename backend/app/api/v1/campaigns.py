from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.database import get_db
from app.models.user import User
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ad_account import AdAccount
from app.models.api_call_log import APICallLog
from app.models.sync_job import SyncJob
from app.schemas.schemas import CampaignSnapshotResponse
from app.utils.security import get_current_user, get_active_ad_account_id, require_module, require_role
from app.services.history_reconciliation import get_history_status
from app.utils.throttle import enforce_cooldown
from app.workers.tasks import sync_ad_account
from typing import List, Optional
import datetime
import io
import logging
import os
import uuid
from fastapi import status
from app.config import settings

logger = logging.getLogger(__name__)

from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.meta_page import MetaPage
from app.models.organization import Organization
from app.schemas.campaign_schemas import (
    CampaignCreateRequest, CampaignRiskPreviewRequest, CampaignPreflightRequest,
)
from app.schemas.schemas import CampaignDraftResponse
from app.schemas.response_schemas import CampaignDetailResponse
from app.services.campaign_validator import CampaignValidator
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_preflight import CampaignPreflight
from app.tenant_rules.campaign_defaults import get_campaign_rules_for_org
from app.services.report_cache import get_report
from app.services.campaign_risk import assess_campaign_risk
from app.services.campaign_proposal import build_campaign_proposal
from app.services.campaign_creation import (
    start_campaign_creation,
    CampaignCapReachedError,
    MAX_CAMPAIGNS_PER_SESSION,
    CAMPAIGN_SESSION_WINDOW_HOURS,
)
from app.schemas.response_schemas import (
    ApiCallHistoryEntry, SuggestionResponse, ImageUploadResponse, RiskPreviewResponse,
    CampaignCreateAcceptedResponse, LearningLogResponse, MessageResponse,
    CampaignPreflightResponse, VideoUploadResponse, SyncLearningResponse,
)
from app.models.account_learning_snapshot import AccountLearningSnapshot

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Nivel de riesgo base de la creación (playbook §2). Ya NO es el valor que se
# devuelve: el riesgo se calcula dinámicamente en `assess_campaign_risk`
# (Ajuste 1). Se conserva como piso conceptual de referencia.
CAMPAIGN_CREATION_RISK_LEVEL = "MEDIUM"

@router.get("/recent", response_model=List[CampaignSnapshotResponse])
async def list_recent_campaign_snapshots(
    only_delivering: bool = False,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna la captura de métricas más reciente para cada campaña de la cuenta de
    anuncios activa (ver `get_active_ad_account_id`).

    `only_delivering=true` deja solo lo que Meta reporta como entregando de
    verdad. Se filtra por `effective_status` y NO por `status`: una campaña
    puede estar ACTIVE y no gastar un sol porque su conjunto está pausado, se
    agotó el presupuesto o la cuenta está topada.

    Los snapshots anteriores a la migración `d4e5f6a7b8c9` tienen
    `effective_status` nulo. Esos se dejan pasar: esconder campañas por un dato
    que todavía no se sincronizó sería peor que mostrarlas: el operador vería
    una pantalla vacía sin saber por qué.
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
            if only_delivering and s.effective_status is not None and s.effective_status != "ACTIVE":
                continue
            latest_snapshots.append(s)

    # 4.3 (E-4): adjuntar el agregado real de 7 días por campaña (mismo cálculo
    # que /reporte, vía el cache 4.2) — el snapshot de un solo día puede tener
    # 0 gasto o un pico atípico y descolocar el ranking "menor CPL".
    responses = [CampaignSnapshotResponse.model_validate(s) for s in latest_snapshots]
    if ad_account_id is not None and responses:
        acc = await db.get(AdAccount, ad_account_id)
        if acc:
            report = await get_report(db, acc, window_days=7)
            agg_by_campaign = {c["campaign_id"]: c for c in report["campaigns"]}
            for r in responses:
                agg = agg_by_campaign.get(r.campaign_id)
                if agg:
                    r.spend_7d = agg["spend"]
                    r.conversations_7d = agg["conversations"]
                    r.cost_per_conv_7d = agg["cost_per_conv"]

    return responses

@router.get("/api-call-history", response_model=List[ApiCallHistoryEntry])
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

@router.get("/{campaign_id}/history", response_model=List[CampaignSnapshotResponse],
            dependencies=[Depends(require_module("campanas"))])
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

@router.get("/{campaign_id}/details", response_model=CampaignDetailResponse,
            dependencies=[Depends(require_module("campanas"))])
async def get_campaign_details(
    campaign_id: str,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Fase 6.9 (Roadmap 12): Detalle completo de una campaña individual con jerarquía
    (AdSets y Ads sincronizados), métricas acumuladas, historial de cambios/acciones
    aplicadas y enlace directo a Meta Ads Manager.
    """
    conditions = [
        CampaignSnapshot.organization_id == current_user.organization_id,
        CampaignSnapshot.campaign_id == campaign_id,
    ]
    if ad_account_id is not None:
        conditions.append(CampaignSnapshot.ad_account_id == ad_account_id)

    q_history = select(CampaignSnapshot).where(*conditions).order_by(CampaignSnapshot.date.asc())
    history_res = (await db.execute(q_history)).scalars().all()

    if not history_res:
        raise HTTPException(status_code=404, detail="Campaña no encontrada en los registros.")

    latest = history_res[-1]
    acc = await db.get(AdAccount, latest.ad_account_id)
    ext_id = acc.external_account_id if acc else "unknown"
    acc_name = acc.name if acc else "Cuenta"

    total_spend = sum(s.spend for s in history_res)
    total_conversions = sum(s.conversions for s in history_res)
    total_impressions = sum(s.impressions for s in history_res)
    total_clicks = sum(s.clicks for s in history_res)
    avg_cpl = total_spend / total_conversions if total_conversions > 0 else total_spend
    avg_ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else latest.ctr

    # Ads Manager URL
    clean_act = ext_id.replace("act_", "")
    ads_manager_url = f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={clean_act}&selected_campaign_ids={campaign_id}"

    # AdSets de esta campaña (más recientes por adset_id)
    from app.models.adset_snapshot import AdsetSnapshot
    q_adsets = (
        select(AdsetSnapshot)
        .where(
            AdsetSnapshot.organization_id == current_user.organization_id,
            AdsetSnapshot.campaign_id == campaign_id,
        )
        .order_by(AdsetSnapshot.date.desc())
    )
    adsets_all = (await db.execute(q_adsets)).scalars().all()
    seen_adsets = set()
    adsets_deduped = []
    for as_snap in adsets_all:
        if as_snap.adset_id not in seen_adsets:
            seen_adsets.add(as_snap.adset_id)
            adsets_deduped.append({
                "adset_id": as_snap.adset_id,
                "name": as_snap.name,
                "budget": as_snap.budget,
                "spend": as_snap.spend,
                "impressions": as_snap.impressions,
                "clicks": as_snap.clicks,
                "conversions": as_snap.conversions,
                "cpl": as_snap.cpl,
                "ctr": as_snap.ctr,
                "targeting_summary": as_snap.targeting_summary or {},
                "date": str(as_snap.date),
            })

    # Ads de esta campaña (más recientes por ad_id)
    from app.models.ad_snapshot import AdSnapshot
    q_ads = (
        select(AdSnapshot)
        .where(
            AdSnapshot.organization_id == current_user.organization_id,
            AdSnapshot.campaign_id == campaign_id,
        )
        .order_by(AdSnapshot.date.desc())
    )
    ads_all = (await db.execute(q_ads)).scalars().all()
    seen_ads = set()
    ads_deduped = []
    for ad_snap in ads_all:
        if ad_snap.ad_id not in seen_ads:
            seen_ads.add(ad_snap.ad_id)
            ads_deduped.append({
                "ad_id": ad_snap.ad_id,
                "adset_id": ad_snap.adset_id,
                "name": ad_snap.name,
                "creative_title": ad_snap.creative_title,
                "creative_body": ad_snap.creative_body,
                "creative_cta_type": ad_snap.creative_cta_type,
                "spend": ad_snap.spend,
                "impressions": ad_snap.impressions,
                "clicks": ad_snap.clicks,
                "conversions": ad_snap.conversions,
                "cpl": ad_snap.cpl,
                "ctr": ad_snap.ctr,
                "date": str(ad_snap.date),
            })

    # Historial de acciones aplicadas o propuestas para esta campaña
    from app.models.ai_action import AIAction
    q_actions = (
        select(AIAction)
        .where(
            AIAction.organization_id == current_user.organization_id,
            AIAction.target_campaign_id == campaign_id,
        )
        .order_by(AIAction.proposed_at.desc())
    )
    actions_res = (await db.execute(q_actions)).scalars().all()
    actions_list = [
        {
            "id": a.id,
            "action_type": a.action_type,
            "risk_level": a.risk_level,
            "title": a.title,
            "description": a.description,
            "status": a.status,
            "proposed_at": a.proposed_at.isoformat(),
            "approved_at": a.approved_at.isoformat() if a.approved_at else None,
            "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            "rejected_at": a.rejected_at.isoformat() if a.rejected_at else None,
            "reject_reason": a.reject_reason,
        }
        for a in actions_res
    ]

    return {
        "campaign_id": latest.campaign_id,
        "campaign_name": latest.campaign_name,
        "status": latest.status,
        "objective": None,
        "ad_account_id": latest.ad_account_id,
        "ad_account_name": acc_name,
        "external_account_id": ext_id,
        "total_spend": round(total_spend, 2),
        "total_conversions": total_conversions,
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "average_cpl": round(avg_cpl, 2),
        "average_ctr": round(avg_ctr, 2),
        "latest_frequency": round(latest.frequency, 2),
        "ads_manager_url": ads_manager_url,
        "adsets": adsets_deduped,
        "ads": ads_deduped,
        "actions": actions_list,
        "history": history_res,
    }

@router.post("/sync-trigger", response_model=MessageResponse)
async def trigger_manual_sync(
    current_user: User = Depends(require_role("OPERATOR")),
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

    task_ids = []
    for acc in accounts:
        task = sync_ad_account.delay(acc.id, "INCREMENTAL")
        task_ids.append(task.id)

    return {
        "status": "queued",
        "task_id": task_ids[0] if task_ids else None,
        "task_ids": task_ids,
        "accounts_queued": len(accounts),
        "message": f"Sincronización encolada para {len(accounts)} cuenta(s) — revisando Meta en segundo plano.",
    }


@router.get("/history-status")
async def get_history_status_endpoint(
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db),
):
    """Cuánto historial falta bajar y cuánto va a tardar.

    El estimado NO es inventado: los meses faltantes salen de reconciliar el
    gasto que Meta reporta por mes contra los snapshots que tenemos, y los
    segundos por llamada del ritmo real medido en `APICallLog` de esta misma
    cuenta. Todo se calcula en Python (regla AI-1).

    `show_estimate` existe para que el frontend no tenga que decidir: solo la
    PRIMERA vez hay algo que estimar. Los syncs incrementales duran segundos y
    mostrar una barra ahí sería ruido.
    """
    if ad_account_id is None:
        return {
            "show_estimate": False, "is_complete": True, "months_total": 0,
            "months_missing": 0, "estimated_seconds": 0, "message": "",
            "first_month": None, "last_month": None, "in_progress": None,
        }

    status = await get_history_status(db, current_user.organization_id, ad_account_id)

    # Avance real si hay un backfill corriendo. `SyncJob.payload_json` lleva
    # meses_hechos/meses_totales — progreso de verdad, no una barra falsa.
    running = (await db.execute(
        select(SyncJob)
        .where(
            SyncJob.organization_id == current_user.organization_id,
            SyncJob.ad_account_id == ad_account_id,
            SyncJob.sync_type == "BACKFILL",
            SyncJob.status == "RUNNING",
        )
        .order_by(desc(SyncJob.started_at))
        .limit(1)
    )).scalar_one_or_none()

    return {
        "show_estimate": not status.is_complete or running is not None,
        "is_complete": status.is_complete,
        "months_total": status.months_total,
        "months_missing": status.months_missing,
        "estimated_seconds": status.estimated_seconds,
        "message": status.estimate_text(),
        "first_month": status.first_month,
        "last_month": status.last_month,
        "in_progress": (running.payload_json or {}) if running else None,
    }

@router.get("/sync-status")
async def get_sync_status(
    ad_account_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    3.1: Consulta el estado y progreso en vivo de la sincronización de una cuenta
    (o de la primera cuenta activa de la organización).
    """
    from app.services.meta_ads.sync_progress import SyncProgressTracker

    if not ad_account_id:
        acc = (await db.execute(
            select(AdAccount).where(
                AdAccount.organization_id == current_user.organization_id,
                AdAccount.status == "ACTIVE",
            ).limit(1)
        )).scalar_one_or_none()
        if not acc:
            return {"status": "IDLE", "message": "No hay cuentas activas"}
        ad_account_id = acc.id
    else:
        acc = await db.get(AdAccount, ad_account_id)
        if not acc or acc.organization_id != current_user.organization_id:
            raise HTTPException(status_code=404, detail="Cuenta de anuncios no encontrada.")

    status_data = SyncProgressTracker.get_sync_status(ad_account_id)
    if status_data:
        return status_data

    return {
        "ad_account_id": ad_account_id,
        "status": "IDLE",
        "last_synced_at": acc.last_synced_at.isoformat() if acc.last_synced_at else None,
        "last_sync_error_code": acc.last_sync_error_code,
        "last_sync_error_detail": acc.last_sync_error_detail,
    }


@router.get("/suggestion", response_model=SuggestionResponse)
async def get_campaign_suggestion(
    ad_account_id: str,
    product_name: str = "Producto",
    image_path: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Fase 3C del plan de creación de campañas: une la sugerencia estructural
    determinista (3A, `CampaignSuggester` — sin cifras inventadas, AI-1) con el
    copy generado por el LLM (3B, `OpenRouterClient.suggest_campaign_copy`) para
    pre-rellenar el formulario de "Crear campaña" con datos reales de la cuenta,
    en vez de que el cliente empiece desde cero.

    `image_path` (F6, opcional): la foto ya subida vía POST /upload-image. El
    formulario vuelve a llamar aquí con ella para que el copy se regenere
    viendo la imagen real. Se valida que pertenezca a la organización: es una
    ruta que llega del cliente y termina leyendo del disco.
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

    return await build_campaign_proposal(db, current_user, ad_account, product_name, image_path)


_ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB


def _webp_to_png(data: bytes) -> bytes:
    """Convierte bytes WebP a PNG (Meta /adimages no admite WebP). Preserva
    transparencia; aplana modos de paleta a RGBA."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        if img.mode in ("P", "LA"):
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()


@router.post("/upload-image", response_model=ImageUploadResponse)
async def upload_campaign_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("OPERATOR")),
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

    # Meta /adimages NO admite WebP (rechaza con error_subcode 1487411, "tipo de
    # archivo no admitido"), pero es un formato común (fotos descargadas, Mac).
    # Lo convertimos a PNG aquí para que el WebP del usuario funcione sin que
    # tenga que reconvertirlo a mano. JPG/PNG se guardan tal cual.
    if file.content_type == "image/webp":
        try:
            contents = _webp_to_png(contents)
            ext = ".png"
        except Exception:
            logger.exception("No se pudo convertir el WebP a PNG")
            raise HTTPException(
                status_code=400,
                detail="No se pudo procesar la imagen WebP. Súbela en JPG o PNG.",
            )

    org_dir = os.path.join(settings.MEDIA_ROOT, current_user.organization_id)
    os.makedirs(org_dir, exist_ok=True)
    filename = f"{uuid.uuid4()}{ext}"
    with open(os.path.join(org_dir, filename), "wb") as f:
        f.write(contents)

    return {"image_path": f"{current_user.organization_id}/{filename}"}


_ALLOWED_VIDEO_TYPES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}
# Subida simple (no chunked). Por encima de esto habría que implementar el
# protocolo por partes de Meta (upload_phase start/transfer/finish).
_MAX_VIDEO_BYTES = 100 * 1024 * 1024


@router.post("/upload-video", response_model=VideoUploadResponse)
async def upload_campaign_video(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("OPERATOR")),
):
    """Sube un video para usar en la creación de campaña (playbook §5 Paso 4:
    "el video suele rendir mejor que la imagen estática").

    Gemelo de `/upload-image`: guarda en el disco compartido y devuelve la ruta;
    la Saga lo lee del mismo volumen y lo sube a `/{act}/advideos`. Antes había
    que conseguir el `video_id` y la miniatura a mano fuera de la app, aunque
    `CreativeBuilder` ya sabía armar el `video_data`.
    """
    ext = _ALLOWED_VIDEO_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa MP4 o MOV.")

    contents = await file.read()
    if len(contents) > _MAX_VIDEO_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"El video supera el límite de {_MAX_VIDEO_BYTES // 1024 // 1024}MB.",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")

    org_dir = os.path.join(settings.MEDIA_ROOT, current_user.organization_id)
    os.makedirs(org_dir, exist_ok=True)
    filename = f"{uuid.uuid4()}{ext}"
    with open(os.path.join(org_dir, filename), "wb") as f:
        f.write(contents)

    return {"video_path": f"{current_user.organization_id}/{filename}"}


@router.post("/preflight", response_model=CampaignPreflightResponse)
async def preflight_campaign(
    request: CampaignPreflightRequest,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db),
):
    """Valida contra Meta los payloads que enviaría la Saga (`validate_only`)
    SIN crear nada: campaña, adset y creativo de una sola pasada.

    Existe porque las reglas de Meta se estaban descubriendo de a una por
    intento real, y cada intento fallido dejaba una campaña huérfana en la
    cuenta del cliente. Valida los MISMOS payloads que construye la Saga, no
    una copia — ver app/services/campaign_preflight.py.
    """
    q_acc = select(AdAccount).where(
        AdAccount.id == request.ad_account_id,
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status != "ARCHIVED",
    )
    ad_account = (await db.execute(q_acc)).scalar_one_or_none()
    if not ad_account:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    org = await db.get(Organization, current_user.organization_id)
    rules = get_campaign_rules_for_org(org)

    # Las señales del histórico son las mismas que usaría la Saga.
    suggestion = await CampaignSuggester(db).suggest(ad_account, product_name=request.product_name)

    # Mismos datos derivados que usaría la Saga: identidad de IG de la página y
    # el nombre inferido del histórico. Así `validate_only` evalúa el payload
    # real, no una aproximación.
    page = (await db.execute(select(MetaPage).where(
        MetaPage.organization_id == current_user.organization_id,
        MetaPage.external_page_id == request.page_id,
    ))).scalar_one_or_none()

    result = await CampaignPreflight(ad_account, rules).run(
        product_name=request.product_name,
        page_id=request.page_id,
        whatsapp_number=request.whatsapp_number,
        budget=request.budget,
        budget_type=request.budget_type,
        days=request.days,
        copy_text=request.copy_text,
        image_hash=request.image_hash,
        headline=request.headline,
        description=request.description,
        instagram_user_id=page.instagram_actor_id if page else None,
        campaign_name=suggestion.get("suggested_campaign_name"),
        best_age_range=suggestion.get("best_age_range"),
        best_device=suggestion.get("best_device"),
        excluded_placements=suggestion.get("excluded_placements") or [],
    )
    return result


@router.post("/risk-preview", response_model=RiskPreviewResponse)
async def preview_campaign_risk(
    request: CampaignRiskPreviewRequest,
    current_user: User = Depends(require_role("OPERATOR")),
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


@router.post("/", status_code=status.HTTP_202_ACCEPTED, response_model=CampaignCreateAcceptedResponse)
async def create_campaign(
    request: CampaignCreateRequest,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db)
):
    """
    Inicia la creación de una campaña en Meta Ads (Saga).

    La lógica real vive en `start_campaign_creation` (CH-1): es el único camino
    de escritura, compartido con el chat de creación de campañas.
    """
    q_acc = select(AdAccount).where(
        AdAccount.id == request.ad_account_id,
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status != "ARCHIVED",
    )
    res_acc = await db.execute(q_acc)
    ad_account = res_acc.scalar_one_or_none()
    if not ad_account:
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    try:
        return await start_campaign_creation(db, current_user, ad_account, request)
    except CampaignCapReachedError as e:
        raise HTTPException(status_code=429, detail=str(e))


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


@router.get("/learning-history", response_model=List[LearningLogResponse])
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


@router.get("/sync-learning-history", response_model=List[SyncLearningResponse])
async def get_sync_learning_history(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    ad_account_id: Optional[str] = Depends(get_active_ad_account_id),
    db: AsyncSession = Depends(get_db),
):
    """Aprendizaje longitudinal por sincronización (Q2): el rendimiento medido de
    la cuenta en cada sync y cómo cambió respecto al anterior, con su veredicto de
    tendencia y observaciones deterministas. Scopeado a la cuenta activa."""
    q = select(AccountLearningSnapshot).where(
        AccountLearningSnapshot.organization_id == current_user.organization_id,
    )
    if ad_account_id:
        q = q.where(AccountLearningSnapshot.ad_account_id == ad_account_id)
    q = q.order_by(desc(AccountLearningSnapshot.created_at)).limit(max(1, min(limit, 100)))

    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": r.id,
            "window_days": r.window_days,
            "verdict": r.verdict,
            "kpis": r.kpis_json,
            "deltas": r.deltas_json,
            "top": r.top_json,
            "bottom": r.bottom_json,
            "segmentation": r.segmentation_json,
            "observations": r.observations_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

