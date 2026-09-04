"""(M-4) response_model de los endpoints que hasta ahora devolvían un dict
plano sin schema declarado — ReportBuilder, kpi_period, ai_usage, learning-
history, api-call-history, ai-models, políticas de negocio y la sugerencia
de campaña. Antes el frontend (`frontend/lib/types.ts`) era la única fuente
de verdad de estas formas, mantenida a mano; con esto el contrato queda
declarado en el backend, `GET /openapi.json` lo expone correctamente, y
`frontend/lib/types.ts` se puede generar desde ahí (`npm run generate-types`)
en vez de mantenerse por separado.
"""
from datetime import date
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


# --- Reporte (ReportBuilder.build / report_cache.get_report) ---

class ReportDeltasPct(BaseModel):
    spend: Optional[float] = None
    conversations: Optional[float] = None
    cost_per_conv: Optional[float] = None


class ReportKpis(BaseModel):
    total_spend: float
    total_reach: int
    total_conversations: int
    avg_ctr: float
    best_cost_per_conv: float
    target_cpl: float
    deltas_pct: ReportDeltasPct


class ReportCampaign(BaseModel):
    campaign_id: str
    name: str
    status: str
    budget: float
    spend: float
    reach: int
    impressions: int
    frequency: float
    ctr: float
    conversations: int
    cost_per_conv: float
    label: str
    spend_delta_pct: Optional[float] = None
    cost_per_conv_delta_pct: Optional[float] = None
    created_by_ai: bool


class ReportAdset(BaseModel):
    adset_id: str
    name: str
    campaign_id: str
    targeting: Dict[str, Any]
    spend: float
    reach: int
    ctr: float
    conversations: int
    cost_per_conv: float


class ReportAd(BaseModel):
    ad_id: str
    name: str
    adset_id: str
    creative_id: Optional[str] = None
    spend: float
    reach: int
    impressions: int
    ctr: float
    conversations: int
    cost_per_conv: float
    creative_title: Optional[str] = None
    creative_body: Optional[str] = None
    creative_cta_type: Optional[str] = None


class ReportSegmentationEntry(BaseModel):
    spend: float
    conversions: int


class ReportSegmentation(BaseModel):
    placements: Dict[str, ReportSegmentationEntry]
    age: Dict[str, ReportSegmentationEntry]
    gender: Dict[str, ReportSegmentationEntry]
    device: Dict[str, ReportSegmentationEntry]
    region: Dict[str, ReportSegmentationEntry]


class ReportAudience(BaseModel):
    name: str
    subtype: str
    description: Optional[str] = None
    approximate_count: Optional[int] = None


class ReportCreative(BaseModel):
    image_hash: str
    name: Optional[str] = None
    url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class ReportAlertEntry(BaseModel):
    name: str
    reason: str


class ReportAlerts(BaseModel):
    red: List[ReportAlertEntry]
    green: List[ReportAlertEntry]


class OptimalParam(BaseModel):
    param: str
    value: str
    justification: str


class ReportAccount(BaseModel):
    name: str
    external_account_id: str
    spend_cap: float
    amount_spent: float
    status: str


class Report(BaseModel):
    window_days: int
    generated_at: str
    account: ReportAccount
    kpis: ReportKpis
    campaigns: List[ReportCampaign]
    adsets: List[ReportAdset]
    ads: List[ReportAd]
    segmentation: ReportSegmentation
    audiences: List[ReportAudience]
    creatives: List[ReportCreative]
    alerts: ReportAlerts
    optimal_params: List[OptimalParam]
    findings: List[str]
    recommendations: List[str]


class BriefDataResponse(BaseModel):
    organization_name: str
    ad_account_name: str
    external_account_id: str
    generated_at: str
    narrative: Optional[str] = None
    narrative_pending: bool
    narrative_generated_at: Optional[str] = None
    narrative_model: Optional[str] = None
    narrative_provider: Optional[str] = None
    narrative_prompt_version: Optional[str] = None
    report: Report
    # Resumen crudo por campaña que alimenta al LLM (Capa 1/2) — forma variable
    # según lo que produce AIEngine, no vale la pena tipar en detalle aquí.
    campaigns_data: Any


# --- KPIs del dashboard (kpi_period.get_kpis_with_deltas) ---

class WindowKpis(BaseModel):
    spend: float
    conversations: int
    cost_per_conv: float


class KpisSummaryResponse(BaseModel):
    kpis_7d: WindowKpis
    kpis_prev_7d: WindowKpis
    deltas_pct: ReportDeltasPct
    critical_alerts_count: int
    last_sync_at: Optional[str] = None


# --- Histórico de gasto mensual ---

class SpendHistoryMonth(BaseModel):
    year_month: str
    spend: float
    closed: bool


class SpendHistoryResponse(BaseModel):
    ad_account_id: str
    months: List[SpendHistoryMonth]


# --- Consumo de tokens de IA ---

class AiUsageFeature(BaseModel):
    feature: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cot_calls: int
    estimated_cost_usd: float


class AiUsageTotals(BaseModel):
    calls: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cot_calls: int
    estimated_cost_usd: float


class AiUsageModel(BaseModel):
    provider: str
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class AiUsageAdAccount(BaseModel):
    ad_account_id: Optional[str] = None
    ad_account_name: Optional[str] = None
    calls: int
    total_tokens: int
    estimated_cost_usd: float


class AiUsageDaily(BaseModel):
    date: str
    calls: int
    total_tokens: int
    estimated_cost_usd: float


class AiUsageResponse(BaseModel):
    days: int
    totals: AiUsageTotals
    by_feature: List[AiUsageFeature]
    by_model: List[AiUsageModel] = Field(default_factory=list)
    by_ad_account: List[AiUsageAdAccount] = Field(default_factory=list)
    daily_timeline: List[AiUsageDaily] = Field(default_factory=list)


# --- Historial de llamadas a Meta ---

class ApiCallHistoryEntry(BaseModel):
    id: str
    ad_account_name: str
    method: str
    endpoint: str
    status_code: int
    timestamp: str


# --- Sugerencia de campaña (CampaignSuggester + copy del LLM) ---

class SuggestionChange(BaseModel):
    change: str
    why: str


class SuggestionAnalysis(BaseModel):
    summary: str
    mode: str
    converged: bool
    # `build_analysis` lo produce desde los resultados reales del outcome loop,
    # pero al no estar declarado aquí el response_model lo borraba de la
    # respuesta: solo llegaba embebido dentro de `summary`. None mientras la IA
    # no tenga ninguna campaña propia evaluada.
    track_record: Optional[str] = None
    maturity_reason: str
    strengths: List[str]
    weaknesses: List[str]
    changes_proposed: List[SuggestionChange]
    past_failures: List[str]
    evaluation_window_days: int
    confidence: str


class SuggestionMaturity(BaseModel):
    converged: bool
    mode: str
    reason: str


class SuggestionWinningCampaign(BaseModel):
    name: str
    cost_per_conv: float
    ctr: float


class SuggestionCopy(BaseModel):
    headline: List[str]
    primary_text: List[str]
    description: str
    welcome_message: str
    suggested_product_name: Optional[str] = None


class SuggestionResponse(BaseModel):
    risk_level: str
    risk_reasons: List[str]
    has_history: bool
    maturity: SuggestionMaturity
    analysis: SuggestionAnalysis
    # (M-2, ver 20-creacion-campanas.md) hoy constantes del playbook — el
    # frontend todavía no los consume, pero el contrato ya los transporta.
    objective: str
    optimization_goal: str
    destination_type: str
    # Nombre con la nomenclatura inferida del histórico real de la cuenta (F3).
    # Se muestra antes de crear, y es el mismo que usa la Saga.
    suggested_campaign_name: Optional[str] = None
    suggested_budget: float
    suggested_days: int
    excluded_placements: List[str]
    best_device: Optional[str] = None
    best_age_range: Optional[str] = None
    winning_campaign: Optional[SuggestionWinningCampaign] = None
    winning_ad_copy: Optional[Dict[str, Any]] = None
    evidence: List[str]
    copy: SuggestionCopy
    suggested_product_name: Optional[str] = None
    # Las campañas históricas comparables que respaldan esta propuesta, con su
    # nivel de confianza y sus afirmaciones marcadas (spec 11).
    #
    # `evidence` (arriba) es la lista de frases que ya existía: describe de qué
    # datos salieron los parámetros. `evidencia` es el conjunto identificable de
    # campañas anteriores. Los dos nombres conviven porque son cosas distintas y
    # fusionarlos perdería la primera.
    #
    # Sin este campo en el modelo, FastAPI lo descartaría en silencio: el
    # servicio lo devuelve y la pantalla no lo vería nunca.
    evidencia: Optional[Dict[str, Any]] = None


# --- Historial de entrenamiento de la IA (creación de campañas) ---

class LearningLogResponse(BaseModel):
    id: str
    product: str
    risk_level: str
    status: str
    params: Optional[Dict[str, Any]] = None
    analysis: Optional[Dict[str, Any]] = None
    maturity: Optional[Dict[str, Any]] = None
    outcome: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None


# --- Aprendizaje longitudinal por sincronización (Q2) ---

class SyncLearningResponse(BaseModel):
    id: str
    window_days: Optional[int] = None
    verdict: Optional[str] = None
    kpis: Optional[Dict[str, Any]] = None
    deltas: Optional[Dict[str, Any]] = None
    top: Optional[List[Dict[str, Any]]] = None
    bottom: Optional[List[Dict[str, Any]]] = None
    segmentation: Optional[Dict[str, Any]] = None
    observations: Optional[List[str]] = None
    created_at: Optional[str] = None


# --- Modelos de IA disponibles por proveedor activo ---

class AiModelOption(BaseModel):
    value: str
    label: str


class AiModelsResponse(BaseModel):
    active_provider: str
    models: List[AiModelOption]
    # El proveedor y el modelo que se van a usar DE VERDAD, y si hay credencial
    # para ellos. La pantalla mostraba la key guardada y el modelo elegido como
    # si estuviera todo conectado, mientras las llamadas salían a otro proveedor
    # sin credencial: el analista contestaba con plantillas y nada se veía roto.
    credential_ok: bool = True
    effective_model: Optional[str] = None
    # Por qué NO hay IA disponible, en texto para la pantalla. Desde la spec 10
    # el proveedor es explícito: si falta la credencial DE ESE proveedor, el
    # sistema no resuelve a otro — lo dice (criterio 8).
    config_error: Optional[str] = None
    # El modelo guardado ya no se ofrece (retirado, en retirada o ausente del
    # catálogo). La organización sigue funcionando; queda SEÑALADA para que
    # alguien le elija otro, en vez de romperse en silencio (criterio 12).
    modelo_no_elegible: bool = False
    modelo_no_elegible_motivo: Optional[str] = None


# --- Políticas de Meta por rubro de negocio ---

class PolicyItem(BaseModel):
    title: str
    description: str


class IndustryOption(BaseModel):
    value: str
    label: str


class BusinessPoliciesResponse(BaseModel):
    industry: Optional[str] = None
    policies: List[PolicyItem]
    available_industries: List[IndustryOption]


# --- Respuestas simples ({"message": "..."}) y de la creación de campañas ---

class MediaAssetResponse(BaseModel):
    """Un medio subido, con su verificación técnica (spec 07).

    `avisos` son accionables y NO predicen rendimiento: el comportamiento de un
    creativo no se lee en sus píxeles, y prometerlo estaría prohibido además de
    ser falso.
    """

    id: str
    kind: str
    content_type: str
    filename: Optional[str] = None
    size_bytes: int
    ancho: Optional[int] = None
    alto: Optional[int] = None
    duracion_segundos: Optional[float] = None
    status: str
    expires_at: Optional[str] = None
    url: str
    avisos: List[Dict[str, Any]] = []


class MediaQuotaResponse(BaseModel):
    usado_bytes: int
    limite_bytes: int
    archivos: int
    limite_archivos: int


class CampaignPerformanceResponse(BaseModel):
    """Rendimiento frente al baseline comparable (spec 06).

    `baseline` lleva siempre el tamaño del conjunto que lo sostiene: una mediana
    sobre tres campañas diminutas y una sobre treinta campañas grandes son
    cifras distintas aunque coincidan, y quien la lea tiene que poder
    distinguirlas.
    """

    baseline: Dict[str, Any]
    campanas: List[Dict[str, Any]]
    frescura: Dict[str, Any]
    umbrales_de_madurez: Dict[str, Any] = {}


class ExperienceResponse(BaseModel):
    """Qué superficie le corresponde a esta sesión (spec 05).

    Informativo, no decisorio: cada endpoint vuelve a comprobar módulo y rol.
    """

    experiencia: str          # "cliente" | "agencia"
    rol: str                  # ADMIN | OPERATOR | VIEWER
    modulos: List[str]
    es_admin_de_plataforma: bool
    organizacion_slug: str


class MonthlyBudgetResponse(BaseModel):
    """Límite mensual de presupuesto y su consumo (spec 02).

    Los montos viajan como cadena y no como `float`: el número que se muestra al
    cliente tiene que ser exactamente el que usó la compuerta para decidir, y
    serializar un decimal a float en el borde reintroduce justo el error que la
    compuerta evita.
    """

    periodo: str          # "YYYY-MM"
    moneda: str
    limite_mensual: str
    gasto_real: str
    comprometido: str
    consumido: str
    margen_disponible: str


class WsTicketResponse(BaseModel):
    """Credencial de un solo uso para el handshake del WebSocket (spec 01)."""

    ticket: str
    expires_in: int


class MessageResponse(BaseModel):
    message: str


class ImageUploadResponse(BaseModel):
    image_path: str


class VideoUploadResponse(BaseModel):
    video_path: str


class RiskPreviewResponse(BaseModel):
    risk_level: str
    risk_reasons: List[str]


class CampaignCreateAcceptedResponse(BaseModel):
    message: str
    draft_id: str
    risk_level: str
    risk_reasons: List[str]
    analysis: SuggestionAnalysis


class PreflightCheck(BaseModel):
    """Resultado de validar UN paso de la Saga contra Meta con validate_only.
    `ok=None` = no se pudo validar (ver `skipped`), no es un éxito."""
    step: str
    ok: Optional[bool] = None
    skipped: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    parent_campaign: Optional[Dict[str, Any]] = None


class PreflightWarning(BaseModel):
    """Aviso que NO bloquea la creación.

    Se separa de `checks` a propósito: un check fallido significa que Meta va a
    rechazar el payload; un aviso significa que la campaña se va a crear igual
    pero hay algo que conviene saber antes. Mezclarlos haría que un consejo
    parezca un error y que el usuario deje de leer los dos.
    """
    code: str
    title: str
    detail: str


class CampaignPreflightResponse(BaseModel):
    ok: bool
    checks: List[PreflightCheck]
    warnings: List[PreflightWarning] = []


# --- Chat de creación de campañas (CH-1..CH-5) ---
# Unión discriminada por `type`: cada variante es una forma de mensaje o
# tarjeta que el frontend renderiza sin adivinar — un solo `switch` sobre
# `type`, nada de campos opcionales sueltos que hay que interpretar.

class ChatAction(BaseModel):
    action: Literal["accept", "reject", "change"]
    label: str
    style: Literal["primary", "danger", "neutral"]


class ChatQuickReply(BaseModel):
    label: str
    value: str


class ChatMessageBase(BaseModel):
    id: str
    role: Literal["assistant", "user", "system"]
    created_at: str


class ChatTextMessage(ChatMessageBase):
    type: Literal["text"] = "text"
    text: str
    quick_replies: List[str] = Field(default_factory=list)
    # De dónde salió la respuesta: modelo, código o simulación (spec 10).
    origen: Optional[str] = None
    origen_label: Optional[str] = None
    # Cuenta, periodo, frescura y suficiencia (spec 12, criterios 3-5). Sin
    # declararlos aquí, FastAPI los descarta en silencio: el motor los pone y la
    # pantalla no los ve nunca.
    contexto: Optional[Dict[str, Any]] = None
    # El resumen de apertura, los avisos proactivos y lo que el sistema entendió
    # del medio adjunto.
    resumen: Optional[Dict[str, Any]] = None
    avisos: Optional[List[Dict[str, Any]]] = None
    medio_contexto: Optional[Dict[str, Any]] = None
    brief: Optional[Dict[str, Any]] = None
    # Cuál de las cuatro situaciones degradadas es, cuando lo es. Es el campo
    # que permite distinguir un fallo de autorización de una cuenta sin datos.
    degradacion: Optional[Dict[str, Any]] = None


class ChatMediaMessage(ChatMessageBase):
    type: Literal["media"] = "media"
    image_path: Optional[str] = None
    video_path: Optional[str] = None


class ChatProposalField(BaseModel):
    key: str
    label: str
    value: str
    source: Literal["ai_suggestion", "user", "account_default", "fixed_rule"]
    editable: bool = True


class ChatMediaPreview(BaseModel):
    image_path: Optional[str] = None
    video_path: Optional[str] = None


class ChatReviewCard(ChatMessageBase):
    type: Literal["review_card"] = "review_card"
    card_id: str
    title: str
    fields: List[ChatProposalField]
    copy_preview: SuggestionCopy
    media_preview: Optional[ChatMediaPreview] = None
    evidence: List[str] = Field(default_factory=list)
    actions: List[ChatAction]


class ChatRiskCard(ChatMessageBase):
    type: Literal["risk_card"] = "risk_card"
    card_id: str
    title: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    risk_reasons: List[str] = Field(default_factory=list)
    preflight_ok: Optional[bool] = None
    preflight_checks: List[PreflightCheck] = Field(default_factory=list)
    actions: List[ChatAction]


class ChatProgressMessage(ChatMessageBase):
    type: Literal["progress"] = "progress"
    draft_id: Optional[str] = None
    step: str
    percent: int
    text: str


class ChatResultMessage(ChatMessageBase):
    type: Literal["result"] = "result"
    draft_id: str
    campaign_id: Optional[str] = None
    adset_id: Optional[str] = None
    ad_id: Optional[str] = None
    campaign_name: Optional[str] = None
    # True cuando el servidor tiene las escrituras a Meta apagadas: la campaña
    # existe acá pero NO en Ads Manager. La UI lo dice en vez de festejar.
    simulated: bool = False
    text: str


class ChatErrorMessage(ChatMessageBase):
    type: Literal["error"] = "error"
    code: Literal["CAP_REACHED", "PREFLIGHT_FAILED", "SAGA_FAILED", "VALIDATION", "INTERNAL"]
    text: str
    retry_after_hours: Optional[int] = None
    actions: List[ChatAction] = Field(default_factory=list)


ChatMessage = Annotated[
    Union[
        ChatTextMessage, ChatMediaMessage, ChatReviewCard, ChatRiskCard,
        ChatProgressMessage, ChatResultMessage, ChatErrorMessage,
    ],
    Field(discriminator="type"),
]


class ChatSlots(BaseModel):
    goal_text: Optional[str] = None
    product_name: Optional[str] = None
    budget: Optional[float] = None
    budget_type: Optional[Literal["LIFETIME", "DAILY"]] = None
    days: Optional[int] = None
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    page_id: Optional[str] = None
    whatsapp_number: Optional[str] = None
    headline: Optional[str] = None
    description: Optional[str] = None
    copy_text: Optional[str] = None


class ChatSessionResponse(BaseModel):
    id: str
    state: str
    status: str
    mode: str = "BUILD"
    ad_account_id: str
    draft_id: Optional[str] = None
    slots: ChatSlots
    messages: List[ChatMessage]
    created_at: str
    updated_at: str


class ChatTurnResponse(BaseModel):
    session_id: str
    state: str
    status: str
    draft_id: Optional[str] = None
    slots: ChatSlots
    messages: List[ChatMessage]


class ChatSessionSummary(BaseModel):
    id: str
    state: str
    status: str
    mode: str = "BUILD"
    ad_account_id: str
    draft_id: Optional[str] = None
    updated_at: str


# --- INVITACIONES (Roadmap 4) ---

class InvitationResponse(BaseModel):
    id: str
    email: str
    role: str
    organization_id: str
    # El enlace solo existe en la respuesta que CREA o reemite la invitación: lo
    # que se guarda es el hash del token, así que al listar no hay nada que
    # devolver. Si hace falta el enlace otra vez, se reemite (y el anterior deja
    # de servir), que es lo correcto: un enlace de invitación es una llave viva.
    token: Optional[str] = None
    invite_url: Optional[str] = None
    expires_at: str
    accepted_at: Optional[str] = None
    created_at: str
    # `False` = la invitación existe pero el correo NO salió (sin proveedor
    # configurado, o el proveedor falló). La pantalla lo dice y ofrece copiar el
    # enlace: sin este dato, el admin cree que el invitado ya lo recibió.
    # `None` al listar invitaciones viejas, donde el dato no se guardó.
    email_sent: Optional[bool] = None


class InvitationDetailsResponse(BaseModel):
    email: str
    role: str
    organization_name: str
    organization_slug: str
    is_expired: bool
    is_accepted: bool


# --- REGLAS DE CAMPAÑA Y UMBRALES IA (Roadmap 6) ---

class CampaignRulesConfigResponse(BaseModel):
    rules: Dict[str, Any]
    overrides: Dict[str, Any]
    defaults: Dict[str, Any]


class AIThresholdsConfigResponse(BaseModel):
    thresholds: Dict[str, Any]
    overrides: Dict[str, Any]
    defaults: Dict[str, Any]


# --- DRILL-DOWN CAMPAÑA INDIVIDUAL (Roadmap 12 / Fase 6.9) ---

class CampaignAdsetDetail(BaseModel):
    adset_id: str
    name: str
    budget: float
    spend: float
    impressions: int
    clicks: int
    conversions: int
    cpl: float
    ctr: float
    targeting_summary: Dict[str, Any] = Field(default_factory=dict)
    date: str


class CampaignAdDetail(BaseModel):
    ad_id: str
    adset_id: str
    name: str
    creative_title: Optional[str] = None
    creative_body: Optional[str] = None
    creative_cta_type: Optional[str] = None
    spend: float
    impressions: int
    clicks: int
    conversions: int
    cpl: float
    ctr: float
    date: str


class CampaignActionDetail(BaseModel):
    id: str
    action_type: str
    risk_level: str
    title: str
    description: str
    status: str
    proposed_at: str
    approved_at: Optional[str] = None
    executed_at: Optional[str] = None
    rejected_at: Optional[str] = None
    reject_reason: Optional[str] = None


class CampaignSnapshotItem(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    objective: Optional[str] = None
    status: str
    budget: float
    spend: float
    reach: int = 0
    impressions: int
    clicks: int
    conversions: int
    conversations: int = 0
    cpc: float = 0.0
    cpl: float
    ctr: float
    frequency: float
    placements_breakdown: Optional[Dict[str, Any]] = None
    demographics_breakdown: Optional[Dict[str, Any]] = None
    date: Union[str, date]

    model_config = ConfigDict(from_attributes=True)


class CampaignDetailResponse(BaseModel):
    campaign_id: str
    campaign_name: str
    status: str
    objective: Optional[str] = None
    ad_account_id: str
    ad_account_name: Optional[str] = None
    external_account_id: str
    total_spend: float
    total_conversions: int
    total_impressions: int
    total_clicks: int
    average_cpl: float
    average_ctr: float
    latest_frequency: float
    ads_manager_url: str
    adsets: List[CampaignAdsetDetail] = Field(default_factory=list)
    ads: List[CampaignAdDetail] = Field(default_factory=list)
    actions: List[CampaignActionDetail] = Field(default_factory=list)
    history: List[CampaignSnapshotItem] = Field(default_factory=list)



# --- Gobernanza del LLM (spec 10) ---

class AiBudgetResponse(BaseModel):
    """Techo mensual de IA, consumo y margen. Los montos viajan como TEXTO.

    Un `Decimal` serializado a `float` en el borde de salida vuelve a introducir
    el residuo binario que la spec 02 saca de la decisión, y esta cifra es la que
    la agencia usa para decidir si amplía el techo.
    """

    moneda: str = "USD"
    limite_usd: Optional[str] = None       # None = sin techo configurado
    consumido_usd: str
    margen_usd: Optional[str] = None
    proporcion: Optional[float] = None     # para la barra de progreso, no decide nada
    umbral_alcanzado: Optional[int] = None  # 70 | 90 | 100
    agotado: bool
    sin_techo: bool
    desde: str


class AiDiscardMotivo(BaseModel):
    motivo: str
    feature: str
    aplicado: bool
    veces: int


class AiDiscardsResponse(BaseModel):
    days: int
    llamadas: int
    aplicados: int
    solo_registrados: int
    # `None` cuando no hubo llamadas: una tasa de descarte sin llamadas no
    # significa "perfecto", significa "sin datos".
    tasa: Optional[float] = None
    modo_registro: bool
    por_motivo: List[AiDiscardMotivo]
