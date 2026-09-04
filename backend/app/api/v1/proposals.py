"""Ciclo de revisión de una propuesta: versión → aprobación → ejecución (spec 03).

`POST /campaigns/` sigue siendo el camino directo: el operador que llama es quien
autoriza, y la versión se congela, se aprueba y se consume en una sola operación.

Estos endpoints separan los tres pasos, que es lo que hace falta cuando alguien
revisa y modifica una propuesta antes de aprobarla:

* crear una versión nueva a partir de una modificación (invalida la aprobación
  anterior y recalcula las compuertas),
* aprobar UNA versión identificable,
* ejecutarla consumiendo esa autorización exactamente una vez.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_proposal import (
    APPROVAL_CONSUMED,
    APPROVAL_PENDING,
    CampaignApproval,
    CampaignProposalVersion,
    MetaWriteIntent,
)
from app.models.proposal_evidence import (
    MOTIVOS_DE_CAMBIO,
    MOTIVOS_LEGIBLES,
    MOTIVOS_QUE_EXIGEN_TEXTO,
    REGLA_PROPUESTA,
    CandidateRule,
    ProposalChange,
    ProposalEvidence,
)
from app.models.user import User
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.budget_gate import (
    BudgetLimitExceeded, MixedCurrencies, assert_cabe_en_el_mes,
)
from app.services.campaign_risk import assess_campaign_risk
from app.services.campaign_suggester import CampaignSuggester
from app.services.campaign_validator import CampaignValidator
from app.services.proposal_versioning import CAMPOS_MATERIALES
from app.services.recuperacion import (
    MotivoRequerido,
    resolver_regla_candidata,
    detectar_reglas_candidatas,
    diff_de_configuracion,
    registrar_cambio_humano,
    validar_motivo,
)
from app.services.proposal_versioning import (
    AprobacionInvalida, aprobar, consumir_aprobacion, crear_version,
    es_cambio_material,
)
from app.utils.security import (
    get_current_user, require_agency, require_granted_ad_account, require_role,
    require_verified_session,
)

logger = logging.getLogger("lejovista.proposals")

router = APIRouter(prefix="/proposals", tags=["proposals"])


class VersionCreateRequest(BaseModel):
    """Una modificación de la propuesta. Produce la versión siguiente."""

    config: CampaignCreateRequest
    # El motivo es obligatorio: convierte la revisión en aprendizaje auditable
    # en vez de en un cambio anónimo. Lo consume el Outcome loop (spec 11).
    change_reason: str = Field(..., min_length=3, max_length=500)
    # Y ESTRUCTURADO (spec 11, criterio 11). El texto libre solo no sirve para
    # detectar que un mismo problema se repite: "subí el presupuesto", "poco
    # presupuesto" y "no alcanzaba" son el mismo motivo escrito de tres formas,
    # y agruparlos exigiría interpretar texto — que es justo lo que no puede
    # decidir el sistema de aprendizaje.
    #
    # El conjunto acotado es lo que permite detectar patrones; el texto libre de
    # arriba es lo que evita que el conjunto acotado mienta.
    change_reason_code: str = Field(..., description="Uno de MOTIVOS_DE_CAMBIO")


class VersionResponse(BaseModel):
    id: str
    draft_id: str
    version_number: int
    material_fingerprint: str
    risk_level: Optional[str] = None
    gates: Dict[str, Any] = {}
    change_reason: Optional[str] = None
    approval_id: Optional[str] = None
    approval_status: Optional[str] = None
    material_change: Optional[bool] = None


class AuditResponse(BaseModel):
    """El recorrido completo de una solicitud de campaña (specs 03 y 09).

    Actor, rol, Organización, AdAccount, solicitud, medios, evidencia, propuesta,
    versiones, cambios, aprobaciones, validaciones y resultado externo saneado.
    Las piezas ya existían dispersas; lo que faltaba era poder recorrerlas juntas
    a partir de una sola pregunta.
    """

    draft_id: str
    status: str
    organizacion_id: Optional[str] = None
    solicitada_por: Optional[Dict[str, Any]] = None
    solicitada_en: Optional[str] = None
    aprobada_por: Optional[Dict[str, Any]] = None
    aprobada_en: Optional[str] = None
    medios: List[Dict[str, Any]] = []
    evidencia: Optional[Dict[str, Any]] = None
    versions: List[Dict[str, Any]]
    approved_version_id: Optional[str] = None
    approved_by_user_id: Optional[str] = None
    external_ids: Dict[str, Optional[str]] = {}
    writes: List[Dict[str, Any]] = []


async def _draft_de_la_organizacion(db: AsyncSession, user: User, draft_id: str) -> CampaignDraft:
    draft = await db.get(CampaignDraft, draft_id)
    if draft is None or draft.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada.")
    return draft


async def _version_de_la_organizacion(
    db: AsyncSession, user: User, version_id: str
) -> CampaignProposalVersion:
    version = await db.get(CampaignProposalVersion, version_id)
    if version is None or version.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Versión no encontrada.")
    return version


async def _aprobacion_de(db: AsyncSession, version_id: str) -> Optional[CampaignApproval]:
    return (await db.execute(
        select(CampaignApproval).where(CampaignApproval.version_id == version_id)
    )).scalar_one_or_none()


@router.post("/drafts/{draft_id}/versions", response_model=VersionResponse,
             status_code=status.HTTP_201_CREATED)
async def crear_version_de_propuesta(
    draft_id: str,
    payload: VersionCreateRequest,
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db),
):
    """Congela una modificación como la versión siguiente.

    Toda versión nueva invalida la aprobación anterior. Y cuando el cambio es
    MATERIAL se vuelven a ejecutar las compuertas —presupuesto, riesgo y
    preflight— sobre la configuración nueva: aprobar una versión cuyo riesgo se
    calculó sobre otra sería autorizar a ciegas.
    """
    draft = await _draft_de_la_organizacion(db, current_user, draft_id)
    if draft.status not in ("DRAFT", "PENDING_CREATION", "FAILED"):
        raise HTTPException(
            status_code=400,
            detail=f"La propuesta está en estado {draft.status}: ya no admite versiones nuevas.",
        )

    nueva_config = payload.config.model_dump()
    ultima = (await db.execute(
        select(CampaignProposalVersion)
        .where(CampaignProposalVersion.draft_id == draft_id)
        .order_by(CampaignProposalVersion.version_number.desc())
        .limit(1)
    )).scalar_one_or_none()

    material = True if ultima is None else es_cambio_material(ultima.config_json, nueva_config)

    ad_account = await require_granted_ad_account(
        db, current_user, nueva_config["ad_account_id"], include_archived=False
    )

    risk_level = ultima.risk_level if ultima is not None else None
    gates: Dict[str, Any] = dict(ultima.gates_json) if ultima is not None else {}

    if material:
        # Historia 43: cada versión modificada repite presupuesto, riesgo,
        # preflight y validaciones.
        await CampaignValidator(ad_account, db=db).validate(payload.config)
        budget_total = payload.config.total_budget()
        try:
            estado = await assert_cabe_en_el_mes(db, current_user.organization_id, budget_total)
        except BudgetLimitExceeded as e:
            raise HTTPException(status_code=400, detail=e.as_detail())
        except MixedCurrencies as e:
            raise HTTPException(status_code=400, detail=e.as_detail())

        sugerencia = await CampaignSuggester(db).suggest(
            ad_account, product_name=payload.config.product_name
        )
        riesgo = assess_campaign_risk(
            budget=budget_total, copy_text=payload.config.copy_text, suggestion=sugerencia,
        )
        risk_level = riesgo["level"]
        gates = {
            "budget_total": float(budget_total),
            "risk_reasons": riesgo["reasons"],
            "margen_disponible": str(estado.margen),
            "preflight": "ok",
        }

    # El motivo se valida ANTES de crear la versión. Crearla y fallar después
    # dejaría una versión sin motivo, que es exactamente lo que el criterio 11
    # impide: sin motivo, la modificación no se guarda.
    try:
        motivo, detalle = validar_motivo(payload.change_reason_code, payload.change_reason)
    except MotivoRequerido as e:
        raise HTTPException(status_code=400, detail=e.as_detail())

    version = await crear_version(
        db,
        draft_id=draft_id,
        organization_id=current_user.organization_id,
        ad_account_id=ad_account.id,
        config=nueva_config,
        risk_level=risk_level,
        gates=gates,
        created_by_user_id=current_user.id,
        change_reason=payload.change_reason,
    )

    await registrar_cambio_humano(
        db,
        organization_id=current_user.organization_id,
        ad_account_id=ad_account.id,
        motivo=motivo,
        motivo_detalle=detalle,
        cambios=diff_de_configuracion(
            ultima.config_json if ultima is not None else {},
            nueva_config,
            CAMPOS_MATERIALES,
        ),
        draft_id=draft_id,
        version_anterior_id=ultima.id if ultima is not None else None,
        version_nueva_id=version.id,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
    )

    draft.payload = nueva_config
    db.add(draft)
    await db.commit()

    # Un motivo que se repite se propone como REGLA CANDIDATA, nunca se aplica
    # solo (criterio 14). Va después del commit y con su propio manejo: detectar
    # un patrón no puede impedir guardar la versión que lo produjo.
    try:
        nuevas = await detectar_reglas_candidatas(
            db, current_user.organization_id, ad_account_id=ad_account.id,
        )
        if nuevas:
            await db.commit()
    except Exception:
        logger.exception(
            "No se pudieron detectar reglas candidatas para la organización %s.",
            current_user.organization_id,
        )

    return VersionResponse(
        id=version.id, draft_id=draft_id, version_number=version.version_number,
        material_fingerprint=version.material_fingerprint, risk_level=version.risk_level,
        gates=version.gates_json, change_reason=version.change_reason,
        material_change=material,
    )


@router.post("/versions/{version_id}/approve", response_model=VersionResponse)
async def aprobar_version(
    version_id: str,
    current_user: User = Depends(require_role("OPERATOR")),
    _verificada: User = Depends(require_verified_session),
    db: AsyncSession = Depends(get_db),
):
    """Autoriza EXACTAMENTE esta versión.

    No se aprueba "la propuesta": se aprueba una versión identificable, y esa
    versión ya no se puede editar. Es lo que hace que la autorización deje de ser
    ambigua.
    """
    version = await _version_de_la_organizacion(db, current_user, version_id)

    ultima = (await db.execute(
        select(CampaignProposalVersion)
        .where(CampaignProposalVersion.draft_id == version.draft_id)
        .order_by(CampaignProposalVersion.version_number.desc())
        .limit(1)
    )).scalar_one()
    if ultima.id != version.id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "VERSION_SUPERSEDED",
                "message": (
                    f"La versión {version.version_number} quedó atrás: hay una "
                    f"versión {ultima.version_number} posterior. Aprueba esa."
                ),
            },
        )

    try:
        aprobacion = await aprobar(db, version, user_id=current_user.id)
    except AprobacionInvalida as e:
        raise HTTPException(status_code=400, detail=e.as_detail())
    await db.commit()

    return VersionResponse(
        id=version.id, draft_id=version.draft_id, version_number=version.version_number,
        material_fingerprint=version.material_fingerprint, risk_level=version.risk_level,
        gates=version.gates_json, change_reason=version.change_reason,
        approval_id=aprobacion.id, approval_status=aprobacion.status,
    )


@router.post("/versions/{version_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def ejecutar_version(
    version_id: str,
    current_user: User = Depends(require_role("OPERATOR")),
    _verificada: User = Depends(require_verified_session),
    db: AsyncSession = Depends(get_db),
):
    """Consume la autorización y lanza la Saga. Exactamente una vez.

    Una segunda llamada sobre la misma autorización NO lanza otra Saga: devuelve
    el resultado de la primera. Es la diferencia entre un doble clic inocuo y una
    campaña duplicada gastando el doble.
    """
    from app.workers.creation_tasks import (
        task_create_ad, task_create_adset, task_create_campaign,
        task_create_creative, task_handle_chain_error, task_upload_image,
        task_verify_campaign,
    )
    from celery import chain

    version = await _version_de_la_organizacion(db, current_user, version_id)
    aprobacion = await _aprobacion_de(db, version_id)
    if aprobacion is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "APPROVAL_MISSING", "message": "Esta versión no está aprobada."},
        )

    if aprobacion.status == APPROVAL_CONSUMED:
        # Idempotencia de la superficie: devolver lo ya ocurrido, no un error y
        # tampoco una ejecución nueva.
        draft = await db.get(CampaignDraft, version.draft_id)
        return {
            "message": "Esta autorización ya se usó; se devuelve el resultado de la primera ejecución.",
            "draft_id": version.draft_id,
            "approval_id": aprobacion.id,
            "already_executed": True,
            "result": (draft.payload or {}).get("result", {}) if draft else {},
        }

    try:
        aprobacion = await consumir_aprobacion(db, aprobacion.id)
    except AprobacionInvalida as e:
        raise HTTPException(status_code=400, detail=e.as_detail())
    await db.commit()

    config = dict(version.config_json)
    ad_account = await require_granted_ad_account(
        db, current_user, config["ad_account_id"], include_archived=False
    )
    sugerencia = await CampaignSuggester(db).suggest(
        ad_account, product_name=config["product_name"]
    )

    contexto = {
        "draft_id": version.draft_id,
        "approval_id": aprobacion.id,
        "version_id": version.id,
        "tenant_id": version.organization_id,
        "ad_account_id": config["ad_account_id"],
        "page_id": config.get("page_id"),
        "product_name": config["product_name"],
        "campaign_name": sugerencia.get("suggested_campaign_name"),
        "budget": config["budget"],
        "budget_type": config.get("budget_type", "LIFETIME"),
        "days": config.get("days"),
        "whatsapp_number": config.get("whatsapp_number"),
        "image_url": config.get("image_url"),
        "image_hash": config.get("image_hash"),
        "image_path": config.get("image_path"),
        "video_id": config.get("video_id"),
        "video_path": config.get("video_path"),
        "thumbnail_url": config.get("thumbnail_url"),
        "copy_text": config.get("copy_text"),
        "headline": config.get("headline"),
        "description": config.get("description"),
        "best_age_range": sugerencia.get("best_age_range"),
        "best_device": sugerencia.get("best_device"),
        "excluded_placements": sugerencia.get("excluded_placements") or [],
    }

    draft = await db.get(CampaignDraft, version.draft_id)
    draft.status = "PENDING_CREATION"
    db.add(draft)
    await db.commit()

    chain(
        task_upload_image.s(contexto),
        task_create_campaign.s().set(countdown=40),
        task_create_adset.s().set(countdown=40),
        task_create_creative.s().set(countdown=40),
        task_create_ad.s().set(countdown=40),
        task_verify_campaign.s().set(countdown=40),
    ).on_error(
        task_handle_chain_error.s(version.draft_id, version.organization_id)
    ).apply_async()

    return {
        "message": "Campaign creation started",
        "draft_id": version.draft_id,
        "approval_id": aprobacion.id,
        "version_id": version.id,
        "already_executed": False,
    }


@router.get("/drafts/{draft_id}/audit", response_model=AuditResponse)
async def auditoria_de_la_propuesta(
    draft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reconstruye qué versión se aprobó, quién la aprobó y qué resultó.

    Los identificadores de Meta salen del registro de intenciones, no del
    documento de resultado del Draft: son un campo de primera clase justamente
    para poder responder esta pregunta.
    """
    draft = await _draft_de_la_organizacion(db, current_user, draft_id)

    versiones = (await db.execute(
        select(CampaignProposalVersion)
        .where(CampaignProposalVersion.draft_id == draft_id)
        .order_by(CampaignProposalVersion.version_number)
    )).scalars().all()

    aprobaciones = {
        a.version_id: a
        for a in (await db.execute(
            select(CampaignApproval).where(CampaignApproval.draft_id == draft_id)
        )).scalars().all()
    }

    intenciones = (await db.execute(
        select(MetaWriteIntent)
        .where(MetaWriteIntent.draft_id == draft_id)
        .order_by(MetaWriteIntent.created_at)
    )).scalars().all()

    consumida = next(
        (a for a in aprobaciones.values() if a.status == APPROVAL_CONSUMED), None
    )

    # Quién pidió esta campaña, con qué medios y sobre qué evidencia. La spec 09
    # pide poder reconstruir el recorrido COMPLETO de una solicitud, no solo sus
    # versiones: las piezas ya existían dispersas, lo que faltaba era la
    # correlación.
    from app.models.media_asset import MediaAsset
    from app.models.user import User as UserModel

    medios = (await db.execute(
        select(MediaAsset).where(MediaAsset.draft_id == draft_id)
    )).scalars().all()

    solicitante = None
    if draft.created_by_user_id:
        u = await db.get(UserModel, draft.created_by_user_id)
        solicitante = {"id": u.id, "email": u.email, "nombre": u.full_name} if u else None

    aprobador = None
    if consumida and consumida.approved_by_user_id:
        u = await db.get(UserModel, consumida.approved_by_user_id)
        aprobador = {"id": u.id, "email": u.email, "nombre": u.full_name} if u else None

    return AuditResponse(
        draft_id=draft_id,
        status=draft.status,
        organizacion_id=draft.organization_id,
        solicitada_por=solicitante,
        solicitada_en=draft.created_at.isoformat() if draft.created_at else None,
        aprobada_por=aprobador,
        aprobada_en=consumida.approved_at.isoformat() if consumida and consumida.approved_at else None,
        medios=[
            {"id": m.id, "kind": m.kind, "content_type": m.content_type,
             "filename": m.filename, "size_bytes": m.size_bytes, "status": m.status}
            for m in medios
        ],
        evidencia=(
            versiones[-1].gates_json.get("baseline_congelado") if versiones else None
        ),
        versions=[
            {
                "id": v.id,
                "version_number": v.version_number,
                "material_fingerprint": v.material_fingerprint,
                "risk_level": v.risk_level,
                "gates": v.gates_json,
                "change_reason": v.change_reason,
                "created_by_user_id": v.created_by_user_id,
                "created_at": v.created_at.isoformat(),
                "approval_status": (
                    aprobaciones[v.id].status if v.id in aprobaciones else None
                ),
            }
            for v in versiones
        ],
        approved_version_id=consumida.version_id if consumida else None,
        approved_by_user_id=consumida.approved_by_user_id if consumida else None,
        external_ids={
            i.operation: i.external_id
            for i in intenciones
            if i.external_id
        },
        writes=[
            {
                "operation": i.operation,
                "status": i.status,
                "external_id": i.external_id,
                "attempts": i.attempts,
                "approval_id": i.approval_id,
                "last_error": i.last_error,
            }
            for i in intenciones
        ],
    )


# --------------------------------------------------------------------------
# Evidencia, cambios humanos y reglas candidatas (spec 11)
# --------------------------------------------------------------------------

class MotivoDeCambio(BaseModel):
    code: str
    label: str
    exige_detalle: bool = False


class EvidenciaResponse(BaseModel):
    draft_id: str
    exploratoria: bool
    ventana_dias: Optional[int] = None
    campanas_comparables: List[Dict[str, Any]] = []
    confianza: Dict[str, Any] = {}
    afirmaciones: List[Dict[str, Any]] = []
    excluidas_por_motivo: List[Dict[str, Any]] = []
    baseline_congelado: Dict[str, Any] = {}
    reglas_version: Optional[str] = None
    prompt_version: Optional[str] = None
    modelo: Optional[str] = None
    proveedor: Optional[str] = None
    cambios_humanos: List[Dict[str, Any]] = []


@router.get("/motivos-de-cambio", response_model=List[MotivoDeCambio])
async def listar_motivos_de_cambio(
    _current_user: User = Depends(require_role("OPERATOR")),
):
    """El conjunto acotado de motivos que acepta una modificación.

    Se sirve desde el backend para que la pantalla no mantenga su propia copia:
    dos listas que hay que sincronizar a mano acaban divergiendo, y la que se
    quedaría vieja es la que ve la persona que elige el motivo.
    """
    return [
        MotivoDeCambio(
            code=code,
            label=MOTIVOS_LEGIBLES.get(code, code),
            exige_detalle=code in MOTIVOS_QUE_EXIGEN_TEXTO,
        )
        for code in MOTIVOS_DE_CAMBIO
    ]


@router.get("/drafts/{draft_id}/evidencia", response_model=EvidenciaResponse)
async def evidencia_de_la_propuesta(
    draft_id: str,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """En qué se basó esta propuesta y qué cambió una persona (criterios 1 y 12).

    Solo agencia: es la vista de revisión, y el cliente no negocia la evidencia
    interna de una recomendación (dependencia con `05`).

    Devuelve el registro PERSISTIDO, no uno recalculado. Recalcular daría el
    conjunto comparable de hoy, y la pregunta que responde este endpoint es en
    qué se basó la propuesta cuando se hizo.
    """
    await _draft_de_la_organizacion(db, current_user, draft_id)

    fila = (await db.execute(
        select(ProposalEvidence)
        .where(
            ProposalEvidence.draft_id == draft_id,
            ProposalEvidence.organization_id == current_user.organization_id,
        )
        .order_by(ProposalEvidence.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    cambios = (await db.execute(
        select(ProposalChange)
        .where(
            ProposalChange.draft_id == draft_id,
            ProposalChange.organization_id == current_user.organization_id,
        )
        .order_by(ProposalChange.created_at.asc())
    )).scalars().all()

    cambios_json = [
        {
            "id": c.id,
            "motivo": c.motivo,
            "motivo_legible": MOTIVOS_LEGIBLES.get(c.motivo, c.motivo),
            "motivo_detalle": c.motivo_detalle,
            "cambios": c.cambios_json or {},
            "actor": c.actor_email,
            "fecha": c.created_at.isoformat() if c.created_at else None,
            "version_anterior_id": c.version_anterior_id,
            "version_nueva_id": c.version_nueva_id,
        }
        for c in cambios
    ]

    if fila is None:
        # Sin evidencia persistida: la propuesta es anterior a la spec 11, o su
        # persistencia falló. Se dice, en vez de devolver un objeto vacío que se
        # leería como "no había evidencia comparable" — que es una afirmación
        # muy distinta y mucho peor.
        return EvidenciaResponse(
            draft_id=draft_id,
            exploratoria=False,
            afirmaciones=[{
                "texto": (
                    "Esta propuesta no tiene registro de en qué se basó. Es "
                    "anterior al registro de evidencia, no una propuesta sin "
                    "respaldo."
                ),
                "registro": "hecho_observado",
                "registro_label": "Observado",
                "registro_explicacion": "Está en los datos de tu cuenta.",
            }],
            cambios_humanos=cambios_json,
        )

    return EvidenciaResponse(
        draft_id=draft_id,
        exploratoria=bool(fila.exploratoria),
        ventana_dias=fila.ventana_dias,
        campanas_comparables=list(fila.campanas_fuente or []),
        confianza=dict(fila.confianza_json or {}),
        afirmaciones=list(fila.afirmaciones_json or []),
        excluidas_por_motivo=list(fila.excluidas_json or []),
        baseline_congelado=dict(fila.baseline_json or {}),
        reglas_version=fila.reglas_version,
        prompt_version=fila.prompt_version,
        modelo=fila.modelo,
        proveedor=fila.proveedor,
        cambios_humanos=cambios_json,
    )


class ReglaCandidataResponse(BaseModel):
    id: str
    motivo: str
    motivo_legible: str
    propuesta: str
    ocurrencias: int
    status: str
    ocurrencias_json: List[Dict[str, Any]] = []
    resuelta_en: Optional[str] = None
    nota_de_resolucion: Optional[str] = None


class ResolverReglaRequest(BaseModel):
    aceptar: bool
    nota: Optional[str] = Field(default=None, max_length=1000)


@router.get("/reglas-candidatas", response_model=List[ReglaCandidataResponse])
async def listar_reglas_candidatas(
    incluir_resueltas: bool = False,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Patrones de corrección repetidos, propuestos como regla (criterio 14).

    Ninguna está en vigor: son propuestas para que una persona decida.
    """
    q = select(CandidateRule).where(
        CandidateRule.organization_id == current_user.organization_id
    )
    if not incluir_resueltas:
        q = q.where(CandidateRule.status == REGLA_PROPUESTA)
    filas = (await db.execute(q.order_by(CandidateRule.created_at.desc()))).scalars().all()

    return [
        ReglaCandidataResponse(
            id=r.id,
            motivo=r.motivo,
            motivo_legible=MOTIVOS_LEGIBLES.get(r.motivo, r.motivo),
            propuesta=r.propuesta,
            ocurrencias=r.ocurrencias,
            status=r.status,
            ocurrencias_json=list(r.ocurrencias_json or []),
            resuelta_en=r.resuelta_en.isoformat() if r.resuelta_en else None,
            nota_de_resolucion=r.nota_de_resolucion,
        )
        for r in filas
    ]


@router.post("/reglas-candidatas/{regla_id}/resolver", response_model=ReglaCandidataResponse)
async def resolver_regla(
    regla_id: str,
    payload: ResolverReglaRequest,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Acepta o rechaza una regla candidata (criterios 14 y 15).

    Las dos dejan constancia. El rechazo además abre un plazo de espera: volver
    a proponer lo mismo al día siguiente convierte la propuesta en ruido y
    garantiza que se dejen de leer también las buenas.
    """
    regla = await db.get(CandidateRule, regla_id)
    if regla is None or regla.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Regla candidata no encontrada.")

    try:
        await resolver_regla_candidata(
            db, regla, aceptar=payload.aceptar,
            actor_user_id=current_user.id, nota=payload.nota,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    await db.refresh(regla)

    return ReglaCandidataResponse(
        id=regla.id,
        motivo=regla.motivo,
        motivo_legible=MOTIVOS_LEGIBLES.get(regla.motivo, regla.motivo),
        propuesta=regla.propuesta,
        ocurrencias=regla.ocurrencias,
        status=regla.status,
        ocurrencias_json=list(regla.ocurrencias_json or []),
        resuelta_en=regla.resuelta_en.isoformat() if regla.resuelta_en else None,
        nota_de_resolucion=regla.nota_de_resolucion,
    )


class AprendizajeResponse(BaseModel):
    dias: int
    propuestas: int
    exploratorias: int
    proporcion_exploratorias: Optional[float] = None
    confianza_por_nivel: Dict[str, int] = {}
    comparables_promedio: Optional[float] = None
    cambios_por_motivo: List[Dict[str, Any]] = []
    veredictos: Dict[str, int] = {}
    reglas_candidatas_abiertas: int = 0


@router.get("/aprendizaje", response_model=AprendizajeResponse)
async def resumen_de_aprendizaje(
    dias: int = 90,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Observabilidad del aprendizaje (spec 11, lista de finalización).

    Cuatro números que responden si el sistema está aprendiendo o solo
    proponiendo:

    * **Proporción de exploratorias.** Si no baja con el tiempo, la
      comparabilidad está demasiado estricta o la cuenta no acumula histórico
      utilizable. Las dos causas se arreglan distinto, y sin este número no se
      distinguen.
    * **Confianza y tamaño del conjunto por propuesta.** Un conjunto que no
      crece con los meses significa que la ventana o las condiciones duras están
      recortando de más.
    * **Tasa de modificación humana por motivo.** Es la señal de en qué se
      equivoca el sistema, agrupada por causa y no por campo tocado.
    * **Veredictos del Outcome loop.** Lo único que dice si las propuestas
      funcionaron.
    """
    import datetime as _dt

    from sqlalchemy import func as _func

    from app.models.campaign_learning_log import CampaignLearningLog

    desde = _dt.datetime.utcnow() - _dt.timedelta(days=max(1, dias))
    org_id = current_user.organization_id

    filas = (await db.execute(
        select(ProposalEvidence).where(
            ProposalEvidence.organization_id == org_id,
            ProposalEvidence.created_at >= desde,
        )
    )).scalars().all()

    propuestas = len(filas)
    exploratorias = sum(1 for f in filas if f.exploratoria)

    por_nivel: Dict[str, int] = {}
    for f in filas:
        por_nivel[f.nivel_confianza] = por_nivel.get(f.nivel_confianza, 0) + 1

    cambios = (await db.execute(
        select(ProposalChange.motivo, _func.count().label("veces"))
        .where(
            ProposalChange.organization_id == org_id,
            ProposalChange.created_at >= desde,
        )
        .group_by(ProposalChange.motivo)
        .order_by(_func.count().desc())
    )).all()

    logs = (await db.execute(
        select(CampaignLearningLog).where(
            CampaignLearningLog.organization_id == org_id,
            CampaignLearningLog.created_at >= desde,
            CampaignLearningLog.outcome_json.is_not(None),
        )
    )).scalars().all()
    veredictos: Dict[str, int] = {}
    for log in logs:
        v = (log.outcome_json or {}).get("verdict")
        if v:
            veredictos[v] = veredictos.get(v, 0) + 1

    abiertas = (await db.execute(
        select(_func.count()).select_from(CandidateRule).where(
            CandidateRule.organization_id == org_id,
            CandidateRule.status == REGLA_PROPUESTA,
        )
    )).scalar() or 0

    return AprendizajeResponse(
        dias=dias,
        propuestas=propuestas,
        exploratorias=exploratorias,
        # `None` y no 0 sin propuestas: "sin datos" no es "ninguna exploratoria".
        proporcion_exploratorias=(
            round(exploratorias / propuestas, 4) if propuestas else None
        ),
        confianza_por_nivel=por_nivel,
        comparables_promedio=(
            round(sum(f.campanas_comparables for f in filas) / propuestas, 2)
            if propuestas else None
        ),
        cambios_por_motivo=[
            {
                "motivo": m,
                "motivo_legible": MOTIVOS_LEGIBLES.get(m, m),
                "veces": int(n),
            }
            for m, n in cambios
        ],
        veredictos=veredictos,
        reglas_candidatas_abiertas=int(abiertas),
    )
