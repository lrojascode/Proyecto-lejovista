// 5.5 (E-5): tipos compartidos del frontend — reflejan los schemas Pydantic
// del backend (`schemas/schemas.py`) y las formas de retorno de los
// servicios/endpoints que no tienen un `response_model` (ReportBuilder,
// kpi_period, reports.py, ai_usage.py, campaigns.py /learning-history).
// No inventar campos que el backend no devuelve — si un endpoint cambia,
// este archivo se actualiza junto con él.

// --- AUTH ---
export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  organization_id: string;
  created_at: string;
}

export interface TokenResponse {
  // (S-2) El refresh token viaja solo como cookie httpOnly, nunca aquí.
  access_token: string;
  token_type: string;
  user: User;
}

// --- ORGANIZATION / AD ACCOUNT ---
export interface Organization {
  id: string;
  slug: string;
  name: string;
  ai_model: string;
  max_budget: number;
  status: string;
  industry?: string | null;
  created_at: string;
}

export interface AdAccount {
  id: string;
  platform: string;
  external_account_id: string;
  name: string;
  status: string;
  spend_cap: number;
  amount_spent: number;
  created_at: string;
}

export interface MetaPage {
  id: string;
  external_page_id: string;
  name: string;
  instagram_actor_id?: string | null;
  whatsapp_number?: string | null;
  status: string;
}

export interface BusinessPolicies {
  industry: string | null;
  policies: { title: string; description: string }[];
  available_industries: { value: string; label: string }[];
}

export interface AiModelsResponse {
  active_provider: string;
  models: { value: string; label: string }[];
}

// --- CAMPAIGN SNAPSHOT (/campaigns/recent, /campaigns/{id}/history) ---
export interface CampaignSnapshot {
  id: string;
  campaign_id: string;
  campaign_name: string;
  status: string;
  budget: number;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  cpl: number;
  ctr: number;
  frequency: number;
  placements_breakdown: Record<string, any>;
  demographics_breakdown: Record<string, any>;
  extra_breakdowns: Record<string, any>;
  date: string;
  // 4.3 (E-4): agregado real de los últimos 7 días.
  spend_7d?: number | null;
  conversations_7d?: number | null;
  cost_per_conv_7d?: number | null;
}

// --- INSIGHTS ---
export interface AIInsight {
  id: string;
  ad_account_id: string;
  campaign_id?: string | null;
  level: string;
  category: string;
  title: string;
  description: string;
  suggested_action_type?: string | null;
  is_dismissed: boolean;
  created_at: string;
}

// --- CAMPAIGN DRAFT ---
export interface CampaignDraft {
  id: string;
  name: string;
  payload: Record<string, any>;
  status: string;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

// --- ACTIONS ---
export interface AIAction {
  id: string;
  insight_id?: string | null;
  ad_account_id: string;
  action_type: string;
  risk_level: string;
  title: string;
  description: string;
  target_campaign_id?: string | null;
  target_adset_id?: string | null;
  platform: string;
  parameters: Record<string, any>;
  status: string;
  proposed_at: string;
  approved_at?: string | null;
  approved_by?: string | null;
  executed_at?: string | null;
  execution_result?: Record<string, any> | null;
  estimated_impact?: string | null;
  confidence: number;
  // NOTA: el modelo (`AIAction.reject_reason`) sí guarda esto, pero
  // `AIActionResponse` no lo expone — hoy llega `undefined` en runtime.
  // Ver spawn_task de seguimiento. Tipado como opcional para reflejar la
  // realidad actual del backend, no para asumir que funciona.
  reject_reason?: string | null;
}

// --- LEARNING HISTORY (/campaigns/learning-history) ---
export interface LearningLog {
  id: string;
  product: string;
  risk_level: string;
  status: string;
  params: Record<string, any> | null;
  analysis: Record<string, any> | null;
  maturity: Record<string, any> | null;
  outcome: Record<string, any> | null;
  created_at: string | null;
}

// --- CAMPAIGN SUGGESTION (/campaigns/suggestion) ---
export interface SuggestionChange {
  change: string;
  why: string;
}

export interface SuggestionAnalysis {
  summary: string;
  mode: string;
  converged: boolean;
  maturity_reason: string;
  strengths: string[];
  weaknesses: string[];
  changes_proposed: SuggestionChange[];
  past_failures: string[];
  evaluation_window_days: number;
  confidence: string;
}

export interface Suggestion {
  risk_level: string;
  risk_reasons: string[];
  has_history: boolean;
  maturity: { converged: boolean; mode: string; reason: string };
  analysis: SuggestionAnalysis;
  suggested_budget: number;
  suggested_days: number;
  excluded_placements: string[];
  best_device: string | null;
  best_age_range: string | null;
  winning_campaign: { name: string; cost_per_conv: number; ctr: number } | null;
  evidence: string[];
  // 3.4: 2 variantes de headline/primary_text — el usuario elige en la UI.
  copy: { headline: string[]; primary_text: string[]; description: string; welcome_message: string };
}

// --- REPORT (ReportBuilder.build / report_cache.get_report) ---
export interface ReportDeltasPct {
  spend: number | null;
  conversations: number | null;
  cost_per_conv: number | null;
}

export interface ReportKpis {
  total_spend: number;
  total_reach: number;
  total_conversations: number;
  avg_ctr: number;
  best_cost_per_conv: number;
  target_cpl: number;
  deltas_pct: ReportDeltasPct;
}

export interface ReportCampaign {
  campaign_id: string;
  name: string;
  status: string;
  budget: number;
  spend: number;
  reach: number;
  impressions: number;
  frequency: number;
  ctr: number;
  conversations: number;
  cost_per_conv: number;
  label: string;
  spend_delta_pct: number | null;
  cost_per_conv_delta_pct: number | null;
  created_by_ai: boolean;
}

export interface ReportAdset {
  adset_id: string;
  name: string;
  campaign_id: string;
  targeting: Record<string, any>;
  spend: number;
  reach: number;
  ctr: number;
  conversations: number;
  cost_per_conv: number;
}

export interface ReportAd {
  ad_id: string;
  name: string;
  adset_id: string;
  creative_id: string | null;
  spend: number;
  reach: number;
  impressions: number;
  ctr: number;
  conversations: number;
  cost_per_conv: number;
  creative_title: string | null;
  creative_body: string | null;
  creative_cta_type: string | null;
}

export interface ReportSegmentationEntry {
  spend: number;
  conversions: number;
}

export interface ReportSegmentation {
  placements: Record<string, ReportSegmentationEntry>;
  age: Record<string, ReportSegmentationEntry>;
  gender: Record<string, ReportSegmentationEntry>;
  device: Record<string, ReportSegmentationEntry>;
  region: Record<string, ReportSegmentationEntry>;
}

export interface ReportAudience {
  name: string;
  subtype: string;
  description: string | null;
  approximate_count: number | null;
}

export interface ReportCreative {
  image_hash: string;
  name: string | null;
  url: string | null;
  width: number | null;
  height: number | null;
}

export interface ReportAlerts {
  red: { name: string; reason: string }[];
  green: { name: string; reason: string }[];
}

export interface OptimalParam {
  param: string;
  value: string;
  justification: string;
}

export interface Report {
  window_days: number;
  generated_at: string;
  account: {
    name: string;
    external_account_id: string;
    spend_cap: number;
    amount_spent: number;
    status: string;
  };
  kpis: ReportKpis;
  campaigns: ReportCampaign[];
  adsets: ReportAdset[];
  ads: ReportAd[];
  segmentation: ReportSegmentation;
  audiences: ReportAudience[];
  creatives: ReportCreative[];
  alerts: ReportAlerts;
  optimal_params: OptimalParam[];
  findings: string[];
  recommendations: string[];
}

// --- BRIEF DATA (/reports/brief-data) ---
export interface BriefData {
  organization_name: string;
  ad_account_name: string;
  external_account_id: string;
  generated_at: string;
  narrative: string | null;
  narrative_pending: boolean;
  narrative_generated_at: string | null;
  narrative_model: string | null;
  narrative_provider: string | null;
  narrative_prompt_version: string | null;
  report: Report;
  campaigns_data: any;
}

// --- KPIS SUMMARY (/reports/kpis-summary) ---
export interface WindowKpis {
  spend: number;
  conversations: number;
  cost_per_conv: number;
}

export interface KpisSummary {
  kpis_7d: WindowKpis;
  kpis_prev_7d: WindowKpis;
  deltas_pct: ReportDeltasPct;
  critical_alerts_count: number;
  last_sync_at: string | null;
}

// --- SPEND HISTORY (/reports/spend-history) ---
export interface SpendHistoryMonth {
  year_month: string;
  spend: number;
  closed: boolean;
}

export interface SpendHistory {
  ad_account_id: string;
  months: SpendHistoryMonth[];
}

// --- AI USAGE (/ai-usage) ---
export interface AiUsageFeature {
  feature: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cot_calls: number;
  estimated_cost_usd: number;
}

export interface AiUsageTotals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cot_calls: number;
  estimated_cost_usd: number;
}

export interface AiUsage {
  days: number;
  totals: AiUsageTotals;
  by_feature: AiUsageFeature[];
}

// --- API CALL HISTORY (/campaigns/api-call-history) ---
export interface ApiCallHistoryEntry {
  id: string;
  ad_account_name: string;
  method: string;
  endpoint: string;
  status_code: number;
  timestamp: string;
}
