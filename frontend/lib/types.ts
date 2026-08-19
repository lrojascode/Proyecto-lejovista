// (M-4) Tipos re-exportados desde el schema OpenAPI real del backend
// (`lib/generated-api-types.ts`, generado con `npm run generate-types` a
// partir de `GET /openapi.json`) — ya no se mantienen a mano. Si un endpoint
// cambia de forma en el backend, correr `npm run generate-types` (con el
// backend corriendo) actualiza estos alias automáticamente; no hay que tocar
// este archivo campo por campo.
//
// Los nombres de aquí son los que usa el resto del frontend (`lib/api.ts`,
// páginas) — se mantienen estables aunque el nombre del schema en el backend
// cambie de forma, para no tener que actualizar todos los imports.
import type { components } from "./generated-api-types";

type Schemas = components["schemas"];

// --- AUTH ---
export type User = Schemas["UserResponse"];
export type TokenResponse = Schemas["TokenResponse"];

// --- ORGANIZATION / AD ACCOUNT ---
export type Organization = Schemas["OrganizationResponse"] & {
  display_name?: string | null;
  logo_url?: string | null;
  accent_color?: string | null;
  llm_provider?: string | null;
  has_llm_api_key?: boolean;
  llm_api_key_preview?: string | null;
};

export type TenantBrand = {
  name: string;
  display_name?: string | null;
  logo_url?: string | null;
  accent_color?: string | null;
};
export type AdAccount = Schemas["AdAccountResponse"] & {
  last_sync_error_code?: number | null;
  last_sync_error_detail?: string | null;
  last_synced_at?: string | null;
  token_expires_at?: string | null;
  token_expires_in_days?: number | null;
  token_warning?: boolean | null;
};
export type MetaPage = Schemas["MetaPageResponse"];
export type WhatsAppNumber = Schemas["WhatsAppNumberResponse"];
export type BusinessPolicies = Schemas["BusinessPoliciesResponse"];
export type AiModelsResponse = Schemas["AiModelsResponse"];

// --- INVITACIONES Y EQUIPO ---
export type OrgMember = {
  id: string;
  email: string;
  full_name: string;
  role: "ADMIN" | "OPERATOR" | "VIEWER";
  created_at: string | null;
};

export type Invitation = {
  id: string;
  email: string;
  role: "ADMIN" | "OPERATOR" | "VIEWER";
  organization_id: string;
  token: string;
  invite_url: string;
  expires_at: string;
  accepted_at?: string | null;
  created_at: string;
};

export type InvitationDetails = {
  email: string;
  role: string;
  organization_name: string;
  organization_slug: string;
  is_expired: boolean;
  is_accepted: boolean;
};

// --- REGLAS DE CAMPAÑA Y UMBRALES IA ---
export type CampaignRulesConfig = {
  rules: Record<string, any>;
  overrides: Record<string, any>;
  defaults: Record<string, any>;
};

export type AIThresholdsConfig = {
  thresholds: Record<string, any>;
  overrides: Record<string, any>;
  defaults: Record<string, any>;
};

export type SyncStatusResponse = {
  ad_account_id?: string;
  status: "IDLE" | "RUNNING" | "COMPLETED" | "FAILED";
  step?: number;
  total_steps?: number;
  step_name?: string;
  message?: string;
  counts?: Record<string, number>;
  records_synced?: number;
  summary?: Record<string, number>;
  error_code?: number | null;
  error_detail?: string | null;
  last_synced_at?: string | null;
  last_sync_error_code?: number | null;
  last_sync_error_detail?: string | null;
  updated_at?: string;
};

// --- CAMPAIGN SNAPSHOT (/campaigns/recent, /campaigns/{id}/history) ---
export type CampaignSnapshot = Schemas["CampaignSnapshotResponse"];

// --- INSIGHTS ---
export type AIInsight = Schemas["AIInsightResponse"];

// --- CAMPAIGN DRAFT ---
export type CampaignDraft = Schemas["CampaignDraftResponse"];

// --- ACTIONS ---
// (UX-2) reject_reason/rejected_at ya vienen del backend — antes estaban
// tipados a mano como opcionales con una nota de que llegaban `undefined`.
export type AIAction = Schemas["AIActionResponse"];

// --- LEARNING HISTORY (/campaigns/learning-history) ---
export type LearningLog = Schemas["LearningLogResponse"];

// --- SYNC LEARNING (/campaigns/sync-learning-history) ---
// Tipado a mano (no está en los tipos generados del OpenAPI todavía). Aprendizaje
// longitudinal por sincronización (Q2).
export type SyncLearningKpis = { spend: number; conversations: number; cost_per_conv: number };
export type SyncLearningSnapshot = {
  id: string;
  window_days: number | null;
  verdict: string | null; // IMPROVING | STABLE | DEGRADING | NO_DATA | BASELINE
  kpis: SyncLearningKpis | null;
  deltas: { spend: number | null; conversations: number | null; cost_per_conv: number | null } | null;
  top: Array<{ name: string; cost_per_conv: number; conversations: number }> | null;
  bottom: Array<{ name: string; cost_per_conv: number; conversations: number }> | null;
  segmentation: Record<string, { name: string; cost_per_conv: number; conversations: number }> | null;
  observations: string[] | null;
  created_at: string | null;
};

// --- CAMPAIGN SUGGESTION (/campaigns/suggestion) ---
export type SuggestionChange = Schemas["SuggestionChange"];
export type SuggestionAnalysis = Schemas["SuggestionAnalysis"];
export type Suggestion = Schemas["SuggestionResponse"] & {
  suggested_product_name?: string | null;
  copy: Schemas["SuggestionResponse"]["copy"] & { suggested_product_name?: string | null };
};

// --- REPORT (ReportBuilder.build / report_cache.get_report) ---
export type ReportDeltasPct = Schemas["ReportDeltasPct"];
export type ReportKpis = Schemas["ReportKpis"];
export type ReportCampaign = Schemas["ReportCampaign"];
export type ReportAdset = Schemas["ReportAdset"];
export type ReportAd = Schemas["ReportAd"];
export type ReportSegmentationEntry = Schemas["ReportSegmentationEntry"];
export type ReportSegmentation = Schemas["ReportSegmentation"];
export type ReportAudience = Schemas["ReportAudience"];
export type ReportCreative = Schemas["ReportCreative"];
export type ReportAlerts = Schemas["ReportAlerts"];
export type OptimalParam = Schemas["OptimalParam"];
export type Report = Schemas["Report"];

// --- BRIEF DATA (/reports/brief-data) ---
export type BriefData = Schemas["BriefDataResponse"];

// --- KPIS SUMMARY (/reports/kpis-summary) ---
export type WindowKpis = Schemas["WindowKpis"];
export type KpisSummary = Schemas["KpisSummaryResponse"];

// --- SPEND HISTORY (/reports/spend-history) ---
export type SpendHistoryMonth = Schemas["SpendHistoryMonth"];
export type SpendHistory = Schemas["SpendHistoryResponse"];

// --- AI USAGE (/ai-usage) ---
export type AiUsageFeature = Schemas["AiUsageFeature"];
export type AiUsageTotals = Schemas["AiUsageTotals"];
export type AiUsage = Schemas["AiUsageResponse"];

// --- API CALL HISTORY (/campaigns/api-call-history) ---
export type ApiCallHistoryEntry = Schemas["ApiCallHistoryEntry"];

// --- CHAT DE CREACIÓN Y CONSULTA DE CAMPAÑAS (/campaigns/chat, /chat) ---
export type ChatSessionResponse = Schemas["ChatSessionResponse"] & {
  mode?: "BUILD" | "ASK";
};
export type ChatSessionSummary = Schemas["ChatSessionSummary"] & {
  mode?: "BUILD" | "ASK";
};
export type ChatSlots = Schemas["ChatSlots"];
export type ChatMessage = ChatSessionResponse["messages"][number];
export type ChatTextMessage = Schemas["ChatTextMessage"];
export type ChatMediaMessage = Schemas["ChatMediaMessage"];
export type ChatReviewCard = Schemas["ChatReviewCard"];
export type ChatRiskCard = Schemas["ChatRiskCard"];
export type ChatProgressMessage = Schemas["ChatProgressMessage"];
export type ChatResultMessage = Schemas["ChatResultMessage"];
export type ChatErrorMessage = Schemas["ChatErrorMessage"];
export type ChatAction = Schemas["ChatAction"];
