import { getApiUrl, getTenantSlugFromHostname } from "./config";
import type {
  TokenResponse, User, Organization, AdAccount, MetaPage, BusinessPolicies, AiModelsResponse,
  CampaignSnapshot, AIInsight, CampaignDraft, AIAction, LearningLog, Suggestion,
  BriefData, SpendHistory, KpisSummary, AiUsage, ApiCallHistoryEntry,
} from "./types";

// Intenta renovar el access token usando el refresh token guardado (S-9).
async function tryRefresh(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) return false;
  try {
    const res = await fetch(`${getApiUrl()}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    localStorage.setItem("token", data.access_token);
    if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
    if (data.user) localStorage.setItem("user", JSON.stringify(data.user));
    return true;
  } catch {
    return false;
  }
}

// Interfaz para solicitudes HTTP genéricas
async function apiRequest<T = any>(
  endpoint: string,
  method: string = "GET",
  body?: any,
  customHeaders: Record<string, string> = {},
  retry: boolean = true
): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const baseUrl = getApiUrl();
  const url = `${baseUrl}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...customHeaders,
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  // Si estamos en un subdominio real de BASE_DOMAIN, inyectamos el tenant
  // slug para que el backend lo registre (cliente1.localhost, cliente1.lrojas.dev).
  if (typeof window !== "undefined") {
    const slug = getTenantSlugFromHostname(window.location.hostname);
    if (slug) headers["X-Tenant-Slug"] = slug;

    // Cuenta de anuncios activa (aislamiento entre varias cuentas de la misma
    // organización). Único escritor de esta clave: AdAccountProvider.
    const adAccountId = localStorage.getItem("active_ad_account_id");
    if (adAccountId) headers["X-Ad-Account-Id"] = adAccountId;
  }

  const config: RequestInit = {
    method,
    headers,
  };

  if (body) {
    config.body = JSON.stringify(body);
  }

  try {
    const response = await fetch(url, config);
    
    if (response.status === 401 && typeof window !== "undefined" && !endpoint.includes("/auth/")) {
      // Intentar renovar el token una vez antes de expulsar al usuario (S-9).
      if (retry && (await tryRefresh())) {
        return apiRequest<T>(endpoint, method, body, customHeaders, false);
      }
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      localStorage.removeItem("refresh_token");
      // Evitar redirects infinitos si ya está en login
      if (!window.location.pathname.includes("/login")) {
        window.location.href = "/login";
      }
    }

    if (!response.ok) {
      const errorText = await response.text();
      let errorMessage = "Ha ocurrido un error en el servidor.";
      try {
        const errorJson = JSON.parse(errorText);
        const detail = errorJson.detail;
        // El detail puede ser un string simple o un objeto estructurado
        // (ej. {code, message} de MULTIPLE_AD_ACCOUNTS_NO_SELECTION).
        errorMessage = (typeof detail === "string" ? detail : detail?.message) || errorMessage;
      } catch (e) {}
      throw new Error(errorMessage);
    }

    // Si es una petición de descarga de reporte HTML
    if (headers["Accept"] === "text/html" || endpoint.endsWith("/reports/html")) {
      return (await response.text()) as any;
    }

    return await response.json();
  } catch (error: any) {
    console.error(`Error in API Request [${method}] ${endpoint}:`, error);
    throw error;
  }
}

// Subida de archivos (multipart/form-data) — no puede reusar apiRequest porque
// esa siempre serializa el body a JSON y fija Content-Type: application/json.
// El navegador debe fijar el boundary del multipart él mismo.
async function apiUploadRequest<T = any>(endpoint: string, formData: FormData): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const url = `${getApiUrl()}${endpoint}`;
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (typeof window !== "undefined") {
    const slug = getTenantSlugFromHostname(window.location.hostname);
    if (slug) headers["X-Tenant-Slug"] = slug;
    const adAccountId = localStorage.getItem("active_ad_account_id");
    if (adAccountId) headers["X-Ad-Account-Id"] = adAccountId;
  }

  const response = await fetch(url, { method: "POST", headers, body: formData });
  if (!response.ok) {
    const errorText = await response.text();
    let errorMessage = "No se pudo subir el archivo.";
    try {
      const detail = JSON.parse(errorText).detail;
      errorMessage = (typeof detail === "string" ? detail : detail?.message) || errorMessage;
    } catch {}
    throw new Error(errorMessage);
  }
  return await response.json();
}

// 5.3 (F-3.2): descarga el PDF real del reporte (no puede reusar apiRequest,
// que siempre parsea la respuesta como JSON) — pide el blob con los mismos
// headers de auth/tenant/cuenta activa y dispara la descarga en el navegador.
async function downloadReportPdf(windowDays: number = 30): Promise<void> {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const url = `${getApiUrl()}/api/v1/reports/pdf?window_days=${windowDays}`;
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (typeof window !== "undefined") {
    const slug = getTenantSlugFromHostname(window.location.hostname);
    if (slug) headers["X-Tenant-Slug"] = slug;
    const adAccountId = localStorage.getItem("active_ad_account_id");
    if (adAccountId) headers["X-Ad-Account-Id"] = adAccountId;
  }

  const response = await fetch(url, { headers });
  if (!response.ok) {
    let errorMessage = "No se pudo generar el PDF.";
    try {
      const detail = (await response.json()).detail;
      errorMessage = (typeof detail === "string" ? detail : detail?.message) || errorMessage;
    } catch {}
    throw new Error(errorMessage);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  const filename = filenameMatch?.[1] || "reporte.pdf";

  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export const api = {
  // --- AUTH ---
  login: (data: any) => apiRequest<TokenResponse>("/api/v1/auth/login", "POST", data),
  signup: (data: any) => apiRequest<TokenResponse>("/api/v1/auth/signup", "POST", data),
  getMe: () => apiRequest<User>("/api/v1/auth/me", "GET"),
  logout: (refreshToken: string) => apiRequest("/api/v1/auth/logout", "POST", { refresh_token: refreshToken }),

  // --- ORGANIZATIONS & ACCOUNTS ---
  getOrg: () => apiRequest<Organization>("/api/v1/organizations/my", "GET"),
  updateOrg: (data: any) => apiRequest<Organization>("/api/v1/organizations/my", "PUT", data),
  getAiModels: () => apiRequest<AiModelsResponse>("/api/v1/organizations/ai-models", "GET"),
  getBusinessPolicies: () => apiRequest<BusinessPolicies>("/api/v1/organizations/my/policies", "GET"),
  getAdAccounts: (includeArchived: boolean = false) =>
    apiRequest<AdAccount[]>(`/api/v1/organizations/my/ad-accounts${includeArchived ? "?include_archived=true" : ""}`, "GET"),
  connectAdAccount: (data: any) => apiRequest<AdAccount>("/api/v1/organizations/my/ad-accounts", "POST", data),
  archiveAdAccount: (id: string) => apiRequest(`/api/v1/organizations/my/ad-accounts/${id}/archive`, "POST"),
  restoreAdAccount: (id: string) => apiRequest(`/api/v1/organizations/my/ad-accounts/${id}/restore`, "POST"),

  // --- CAMPAIGNS ---
  getRecentCampaigns: () => apiRequest<CampaignSnapshot[]>("/api/v1/campaigns/recent", "GET"),
  getCampaignHistory: (campaignId: string) => apiRequest<CampaignSnapshot[]>(`/api/v1/campaigns/${campaignId}/history`, "GET"),
  triggerSync: () => apiRequest("/api/v1/campaigns/sync-trigger", "POST"),
  getApiCallHistory: (limit: number = 10) => apiRequest<ApiCallHistoryEntry[]>(`/api/v1/campaigns/api-call-history?limit=${limit}`, "GET"),
  getMyPages: () => apiRequest<MetaPage[]>("/api/v1/organizations/my/pages", "GET"),
  setPageWhatsApp: (pageId: string, whatsappNumber: string) =>
    apiRequest(`/api/v1/organizations/my/pages/${pageId}/whatsapp`, "PATCH", { whatsapp_number: whatsappNumber }),
  getCampaignSuggestion: (adAccountId: string, productName: string) =>
    apiRequest<Suggestion>(
      `/api/v1/campaigns/suggestion?ad_account_id=${encodeURIComponent(adAccountId)}` +
      `&product_name=${encodeURIComponent(productName)}`,
      "GET"
    ),
  createCampaign: (data: any) => apiRequest("/api/v1/campaigns/", "POST", data),
  uploadCampaignImage: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiUploadRequest<{ image_path: string }>("/api/v1/campaigns/upload-image", formData);
  },
  getCampaignDraft: (draftId: string) => apiRequest<CampaignDraft>(`/api/v1/campaigns/drafts/${draftId}`, "GET"),
  previewCampaignRisk: (adAccountId: string, budget: number, copyText: string) =>
    apiRequest("/api/v1/campaigns/risk-preview", "POST", {
      ad_account_id: adAccountId, budget, copy_text: copyText,
    }),
  getLearningHistory: (limit: number = 20) =>
    apiRequest<LearningLog[]>(`/api/v1/campaigns/learning-history?limit=${limit}`, "GET"),

  // --- AI USAGE (contador de tokens) ---
  getAiUsage: (days: number = 30) => apiRequest<AiUsage>(`/api/v1/ai-usage?days=${days}`, "GET"),

  // --- INSIGHTS ---
  getInsights: () => apiRequest<AIInsight[]>("/api/v1/insights", "GET"),
  dismissInsight: (insightId: string) => apiRequest(`/api/v1/insights/${insightId}/dismiss`, "POST"),
  triggerAnalysis: () => apiRequest("/api/v1/insights/analyze-trigger", "POST"),

  // --- ACTIONS ---
  getPendingActions: () => apiRequest<AIAction[]>("/api/v1/actions", "GET"),
  getActionHistory: () => apiRequest<AIAction[]>("/api/v1/actions/history", "GET"),
  approveAction: (actionId: string, approve: boolean, rejectReason?: string) =>
    apiRequest<AIAction>(`/api/v1/actions/${actionId}/approve`, "POST", { approve, reject_reason: rejectReason }),
  getActionStatus: (actionId: string) => apiRequest<AIAction>(`/api/v1/actions/${actionId}/status`, "GET"),

  // --- REPORTS ---
  getBriefData: (windowDays: number = 30) => apiRequest<BriefData>(`/api/v1/reports/brief-data?window_days=${windowDays}`, "GET"),
  getSpendHistory: () => apiRequest<SpendHistory>("/api/v1/reports/spend-history", "GET"),
  getKpisSummary: () => apiRequest<KpisSummary>("/api/v1/reports/kpis-summary", "GET"),
  downloadReportPdf,
};
