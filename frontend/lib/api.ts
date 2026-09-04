import { getApiUrl, getTenantSlugFromHostname } from "./config";
import type {
  TokenResponse, User, Organization, AdAccount, MetaPage, WhatsAppNumber, BusinessPolicies, AiModelsResponse,
  CampaignSnapshot, CampaignDetail, AIInsight, CampaignDraft, AIAction, LearningLog, SyncLearningSnapshot, Suggestion,
  BriefData, SpendHistory, KpisSummary, AiUsage, ApiCallHistoryEntry,
  ChatSessionResponse, ChatSessionSummary, SyncStatusResponse,
  OrgMember, MemberAdAccounts, Invitation, InvitationDetails, TenantBrand, MyModules, HistoryStatus,
  AdminOrganization,
  CampaignRulesConfig, AIThresholdsConfig, MonthlyBudget, Experience,
} from "./types";

// (S-2) El access token vive SOLO en memoria del módulo, nunca en
// localStorage — una vulnerabilidad XSS que ejecute JS puede leerlo mientras
// la pestaña esté abierta (inevitable, es lo que la sesión necesita para
// operar), pero no puede exfiltrarlo leyendo localStorage/almacenamiento
// persistente, ni sobrevive a un F5 o una pestaña nueva por su cuenta — para
// eso existe el refresh silencioso de abajo. El refresh token nunca toca JS:
// viaja como cookie httpOnly que el navegador adjunta solo.
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

function setSession(data: TokenResponse) {
  accessToken = data.access_token;
  if (typeof window !== "undefined" && data.user) {
    localStorage.setItem("user", JSON.stringify(data.user));
  }
}

function clearSession() {
  accessToken = null;
  if (typeof window !== "undefined") {
    localStorage.removeItem("user");
  }
}

// Pide un access token nuevo a partir de la cookie httpOnly del refresh
// (el navegador la adjunta solo — `credentials: "include"`). Devuelve la
// respuesta completa (para el bootstrap de sesión al cargar la página) o
// `null` si no hay sesión válida.
async function refreshSession(): Promise<TokenResponse | null> {
  try {
    const res = await fetch(`${getApiUrl()}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return null;
    const data: TokenResponse = await res.json();
    setSession(data);
    return data;
  } catch {
    return null;
  }
}

// Se llama una vez al montar el layout autenticado: intenta renovar la sesión
// desde la cookie de refresh (sobrevive a recargar la página, ya que el access
// token en memoria no persiste). Si no hay cookie válida, no hay sesión.
export async function bootstrapSession(): Promise<User | null> {
  const data = await refreshSession();
  return data?.user ?? null;
}

// Interfaz para solicitudes HTTP genéricas
async function apiRequest<T = any>(
  endpoint: string,
  method: string = "GET",
  body?: any,
  customHeaders: Record<string, string> = {},
  retry: boolean = true
): Promise<T> {
  const baseUrl = getApiUrl();
  const url = `${baseUrl}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...customHeaders,
  };

  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
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

  // (S-2) credentials: "include" para que el navegador adjunte la cookie
  // httpOnly del refresh en /auth/refresh y /auth/logout (el resto de
  // endpoints la ignoran, pero no hace daño enviarla).
  const config: RequestInit = {
    method,
    headers,
    credentials: "include",
  };

  if (body) {
    config.body = JSON.stringify(body);
  }

  try {
    const response = await fetch(url, config);

    if (response.status === 401 && typeof window !== "undefined" && !endpoint.includes("/auth/")) {
      // Intentar renovar la sesión una vez antes de expulsar al usuario (S-9).
      if (retry && (await refreshSession())) {
        return apiRequest<T>(endpoint, method, body, customHeaders, false);
      }
      clearSession();
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
  const url = `${getApiUrl()}${endpoint}`;
  const headers: Record<string, string> = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  if (typeof window !== "undefined") {
    const slug = getTenantSlugFromHostname(window.location.hostname);
    if (slug) headers["X-Tenant-Slug"] = slug;
    const adAccountId = localStorage.getItem("active_ad_account_id");
    if (adAccountId) headers["X-Ad-Account-Id"] = adAccountId;
  }

  const response = await fetch(url, { method: "POST", headers, body: formData, credentials: "include" });
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
  const url = `${getApiUrl()}/api/v1/reports/pdf?window_days=${windowDays}`;
  const headers: Record<string, string> = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  if (typeof window !== "undefined") {
    const slug = getTenantSlugFromHostname(window.location.hostname);
    if (slug) headers["X-Tenant-Slug"] = slug;
    const adAccountId = localStorage.getItem("active_ad_account_id");
    if (adAccountId) headers["X-Ad-Account-Id"] = adAccountId;
  }

  const response = await fetch(url, { headers, credentials: "include" });
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
  // login/signup guardan la sesión ellos mismos (access token en memoria,
  // user en localStorage) — el refresh token nunca pasa por aquí, lo pone el
  // navegador directo desde la cookie httpOnly que fija el backend.
  login: async (data: any) => {
    const res = await apiRequest<TokenResponse>("/api/v1/auth/login", "POST", data);
    setSession(res);
    return res;
  },
  signup: async (data: any) => {
    const res = await apiRequest<TokenResponse>("/api/v1/auth/signup", "POST", data);
    setSession(res);
    return res;
  },
  getMe: () => apiRequest<User>("/api/v1/auth/me", "GET"),
  logout: async () => {
    try {
      await apiRequest("/api/v1/auth/logout", "POST");
    } finally {
      clearSession();
    }
  },
  bootstrapSession,

  // --- ORGANIZATIONS & ACCOUNTS ---
  getOrg: () => apiRequest<Organization>("/api/v1/organizations/my", "GET"),
  updateOrg: (data: any) => apiRequest<Organization>("/api/v1/organizations/my", "PATCH", data),
  uploadOrgLogo: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiUploadRequest<{ logo_url: string }>("/api/v1/organizations/my/logo", formData);
  },
  getMyModules: () => apiRequest<MyModules>("/api/v1/organizations/my/modules", "GET"),

  // --- Panel de plataforma. Solo responde si el usuario tiene el flag
  //     is_platform_admin; si no, 403.
  adminListOrganizations: () =>
    apiRequest<AdminOrganization[]>("/api/v1/admin/organizations", "GET"),
  adminListModules: () =>
    apiRequest<{ available: Record<string, string>; undisableable: string[] }>(
      "/api/v1/admin/modules", "GET"),
  adminCreateOrganization: (body: Record<string, unknown>) =>
    apiRequest<AdminOrganization>("/api/v1/admin/organizations", "POST", body),
  adminUpdateModules: (orgId: string, modules: string[]) =>
    apiRequest<AdminOrganization>(
      `/api/v1/admin/organizations/${orgId}/modules`, "PATCH", { enabled_modules: modules }),
  adminInviteAdmin: (orgId: string, email: string) =>
    apiRequest<{ message: string }>(
      `/api/v1/admin/organizations/${orgId}/invite-admin`, "POST", { email }),
  getTenantBrand: (slug?: string) =>
    apiRequest<TenantBrand>(
      slug ? `/api/v1/auth/tenant-brand?slug=${encodeURIComponent(slug)}` : "/api/v1/auth/tenant-brand",
      "GET"
    ),
  getAiModels: (provider?: string) =>
    apiRequest<AiModelsResponse>(
      provider ? `/api/v1/organizations/ai-models?provider=${encodeURIComponent(provider)}` : "/api/v1/organizations/ai-models",
      "GET"
    ),
  testLLMKey: (data: { provider: string; api_key: string; model?: string }) =>
    apiRequest<{ valid: boolean; message: string; provider: string }>("/api/v1/organizations/my/llm-key/test", "POST", data),
  getBusinessPolicies: () => apiRequest<BusinessPolicies>("/api/v1/organizations/my/policies", "GET"),
  getAdAccounts: (includeArchived: boolean = false) =>
    apiRequest<AdAccount[]>(`/api/v1/organizations/my/ad-accounts${includeArchived ? "?include_archived=true" : ""}`, "GET"),
  connectAdAccount: (data: any) => apiRequest<AdAccount>("/api/v1/organizations/my/ad-accounts", "POST", data),
  // `appId`/`appSecret` solo cuando el token NO lo emitió la app de la agencia
  // (un System User del Business Manager del cliente). Omitirlos deja la app
  // que ya tenía configurada la cuenta.
  reconnectAdAccount: (id: string, accessToken: string, appId?: string, appSecret?: string) =>
    apiRequest<AdAccount>(`/api/v1/organizations/my/ad-accounts/${id}/reconnect`, "POST", {
      access_token: accessToken,
      ...(appId ? { app_id: appId, app_secret: appSecret } : {}),
    }),
  checkAdAccountToken: (id: string) =>
    apiRequest<AdAccount>(`/api/v1/organizations/my/ad-accounts/${id}/check-token`, "POST"),
  archiveAdAccount: (id: string) => apiRequest(`/api/v1/organizations/my/ad-accounts/${id}/archive`, "POST"),
  restoreAdAccount: (id: string) => apiRequest(`/api/v1/organizations/my/ad-accounts/${id}/restore`, "POST"),
  deleteAdAccount: (id: string) => apiRequest<{ message: string }>(`/api/v1/organizations/my/ad-accounts/${id}`, "DELETE"),

  // --- REGLAS DE CAMPAÑA Y UMBRALES IA (Roadmap 6) ---
  getCampaignRulesConfig: () =>
    apiRequest<CampaignRulesConfig>("/api/v1/organizations/my/campaign-rules", "GET"),
  updateCampaignRulesConfig: (overrides: Record<string, any>) =>
    apiRequest<CampaignRulesConfig>("/api/v1/organizations/my/campaign-rules", "PUT", { overrides }),
  getAIThresholdsConfig: () =>
    apiRequest<AIThresholdsConfig>("/api/v1/organizations/my/ai-thresholds", "GET"),
  updateAIThresholdsConfig: (overrides: Record<string, any>) =>
    apiRequest<AIThresholdsConfig>("/api/v1/organizations/my/ai-thresholds", "PUT", { overrides }),

  // Límite mensual de presupuesto y su consumo. Los montos vienen como cadena:
  // el número que se muestra tiene que ser exactamente el que usó la compuerta
  // para decidir, y pasarlo por `number` reintroduce el error que evita
  // (spec 02).
  // Qué superficie le corresponde a esta sesión (spec 05). Informativo: sirve
  // para armar la navegación, no para decidir permisos — cada endpoint vuelve a
  // comprobar módulo y rol en el backend. Filtrar el menú es experiencia de
  // usuario; lo único que es seguridad son las compuertas.
  getMyExperience: () =>
    apiRequest<Experience>("/api/v1/organizations/my/experience", "GET"),

  getMonthlyBudget: () =>
    apiRequest<MonthlyBudget>("/api/v1/organizations/my/budget", "GET"),

  // --- INVITACIONES Y EQUIPO ---
  // --- MI CUENTA ---
  updateProfile: (data: { full_name?: string; email?: string }) =>
    apiRequest<User>("/api/v1/auth/me", "PATCH", data),
  // Cambiar la contraseña revoca TODAS las sesiones del usuario, incluida la
  // que hace esta llamada: el backend devuelve un par de tokens nuevo y hay que
  // adoptarlo, o la siguiente petición sale con un access token ya inválido y
  // el usuario queda deslogueado por haber cambiado su propia contraseña.
  changePassword: (currentPassword: string, newPassword: string) =>
    apiRequest<TokenResponse>("/api/v1/auth/change-password", "POST", {
      current_password: currentPassword,
      new_password: newPassword,
    }).then((res) => {
      setSession(res);
      return res;
    }),

  // Credencial de un solo uso y 30 segundos de vida para abrir el WebSocket.
  // El access token ya no viaja en la URL de la conexión (spec 01).
  getWsTicket: () =>
    apiRequest<{ ticket: string; expires_in: number }>("/api/v1/auth/ws-ticket", "POST"),
  forgotPassword: (email: string) =>
    apiRequest<{ message: string }>("/api/v1/auth/forgot-password", "POST", { email }),
  resetPassword: (token: string, newPassword: string) =>
    apiRequest<{ message: string }>("/api/v1/auth/reset-password", "POST", {
      token, new_password: newPassword,
    }),

  getOrgMembers: () => apiRequest<OrgMember[]>("/api/v1/organizations/my/members", "GET"),
  updateMemberRole: (userId: string, role: "ADMIN" | "OPERATOR" | "VIEWER") =>
    apiRequest<{ message: string }>(`/api/v1/organizations/my/members/${userId}`, "PATCH", { role }),
  removeMember: (userId: string) =>
    apiRequest<{ message: string }>(`/api/v1/organizations/my/members/${userId}`, "DELETE"),
  getOrgInvitations: () => apiRequest<Invitation[]>("/api/v1/organizations/my/invitations", "GET"),
  createOrgInvitation: (data: {
    email: string;
    role: "ADMIN" | "OPERATOR" | "VIEWER";
    // `undefined` = todas las cuentas de la organización al aceptar; una lista
    // acota (el caso de un cliente que solo debe ver la suya).
    ad_account_ids?: string[];
  }) => apiRequest<Invitation>("/api/v1/organizations/my/invitations", "POST", data),
  getMemberAdAccounts: (userId: string) =>
    apiRequest<MemberAdAccounts>(`/api/v1/organizations/my/members/${userId}/ad-accounts`, "GET"),
  setMemberAdAccounts: (userId: string, adAccountIds: string[]) =>
    apiRequest<{ user_id: string; granted_ids: string[] }>(
      `/api/v1/organizations/my/members/${userId}/ad-accounts`, "PUT",
      { ad_account_ids: adAccountIds },
    ),
  deleteOrgInvitation: (id: string) =>
    apiRequest<{ message: string }>(`/api/v1/organizations/my/invitations/${id}`, "DELETE"),
  getInvitationDetails: (token: string) =>
    apiRequest<InvitationDetails>(`/api/v1/invitations/${token}`, "GET"),
  acceptInvitation: (token: string, data: { full_name: string; password: string }) =>
    apiRequest<TokenResponse>(`/api/v1/invitations/${token}/accept`, "POST", data).then((res) => {
      accessToken = res.access_token;
      return res;
    }),

  // --- CAMPAIGNS ---
  getRecentCampaigns: (onlyDelivering = false) =>
    apiRequest<CampaignSnapshot[]>(
      `/api/v1/campaigns/recent${onlyDelivering ? "?only_delivering=true" : ""}`,
      "GET"
    ),
  getCampaignHistory: (campaignId: string) => apiRequest<CampaignSnapshot[]>(`/api/v1/campaigns/${campaignId}/history`, "GET"),
  getCampaignDetails: (campaignId: string) => apiRequest<CampaignDetail>(`/api/v1/campaigns/${campaignId}/details`, "GET"),
  triggerSync: () => apiRequest<{ status: string; task_id?: string; message: string }>("/api/v1/campaigns/sync-trigger", "POST"),
  getHistoryStatus: () => apiRequest<HistoryStatus>("/api/v1/campaigns/history-status", "GET"),
  getSyncStatus: (adAccountId?: string) =>
    apiRequest<SyncStatusResponse>(`/api/v1/campaigns/sync-status${adAccountId ? `?ad_account_id=${encodeURIComponent(adAccountId)}` : ""}`, "GET"),
  getApiCallHistory: (limit: number = 10) => apiRequest<ApiCallHistoryEntry[]>(`/api/v1/campaigns/api-call-history?limit=${limit}`, "GET"),
  getMyPages: () => apiRequest<MetaPage[]>("/api/v1/organizations/my/pages", "GET"),
  setPageWhatsApp: (pageId: string, whatsappNumber: string) =>
    apiRequest(`/api/v1/organizations/my/pages/${pageId}/whatsapp`, "PATCH", { whatsapp_number: whatsappNumber }),
  refreshPages: () => apiRequest<{ message: string }>("/api/v1/organizations/my/pages/refresh", "POST"),
  setDefaultPage: (adAccountId: string, pageId: string | null) =>
    apiRequest<AdAccount>(`/api/v1/organizations/my/ad-accounts/${adAccountId}/default-page`, "PATCH", { page_id: pageId }),
  // `imagePath` (F6): con la foto ya subida, el copy se regenera viéndola.
  getCampaignSuggestion: (adAccountId: string, productName: string, imagePath?: string) =>
    apiRequest<Suggestion>(
      `/api/v1/campaigns/suggestion?ad_account_id=${encodeURIComponent(adAccountId)}` +
      `&product_name=${encodeURIComponent(productName)}` +
      (imagePath ? `&image_path=${encodeURIComponent(imagePath)}` : ""),
      "GET"
    ),
  createCampaign: (data: any) => apiRequest("/api/v1/campaigns/", "POST", data),
  uploadCampaignImage: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiUploadRequest<{ image_path: string }>("/api/v1/campaigns/upload-image", formData);
  },
  uploadCampaignVideo: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiUploadRequest<{ video_path: string }>("/api/v1/campaigns/upload-video", formData);
  },

  // --- NÚMEROS DE WHATSAPP (catálogo de la organización, F1) ---
  getWhatsAppNumbers: () =>
    apiRequest<WhatsAppNumber[]>("/api/v1/organizations/my/whatsapp-numbers", "GET"),
  addWhatsAppNumber: (displayNumber: string) =>
    apiRequest<WhatsAppNumber>("/api/v1/organizations/my/whatsapp-numbers", "POST", {
      display_number: displayNumber,
    }),
  // `adAccountIds` omitido deja el alcance como está; una lista lo REEMPLAZA.
  // Los números MANUAL no se pueden reconciliar contra Meta, así que acotarlos
  // a las cuentas que corresponden es un acto del usuario.
  updateWhatsAppNumber: (id: string, displayNumber: string, adAccountIds?: string[]) =>
    apiRequest<WhatsAppNumber>(`/api/v1/organizations/my/whatsapp-numbers/${id}`, "PATCH", {
      display_number: displayNumber,
      ...(adAccountIds ? { ad_account_ids: adAccountIds } : {}),
    }),
  deleteWhatsAppNumber: (id: string) =>
    apiRequest<{ message: string }>(`/api/v1/organizations/my/whatsapp-numbers/${id}`, "DELETE"),
  getCampaignDraft: (draftId: string) => apiRequest<CampaignDraft>(`/api/v1/campaigns/drafts/${draftId}`, "GET"),
  previewCampaignRisk: (adAccountId: string, budget: number, copyText: string) =>
    apiRequest("/api/v1/campaigns/risk-preview", "POST", {
      ad_account_id: adAccountId, budget, copy_text: copyText,
    }),
  getLearningHistory: (limit: number = 20) =>
    apiRequest<LearningLog[]>(`/api/v1/campaigns/learning-history?limit=${limit}`, "GET"),

  // --- CHAT DE CREACIÓN Y CONSULTA DE CAMPAÑAS ---
  startCampaignChat: (adAccountId?: string, mode: "BUILD" | "ASK" = "BUILD") =>
    apiRequest<ChatSessionResponse>("/api/v1/campaigns/chat/sessions", "POST", {
      ad_account_id: adAccountId ?? null,
      mode,
    }),
  listCampaignChatSessions: () =>
    apiRequest<ChatSessionSummary[]>("/api/v1/campaigns/chat/sessions", "GET"),
  getCampaignChatSession: (sessionId: string) =>
    apiRequest<ChatSessionResponse>(`/api/v1/campaigns/chat/sessions/${sessionId}`, "GET"),
  sendCampaignChatMessage: (
    sessionId: string,
    data: { clientTurnId: string; text?: string; imagePath?: string; videoPath?: string }
  ) =>
    apiRequest<ChatSessionResponse>(`/api/v1/campaigns/chat/sessions/${sessionId}/messages`, "POST", {
      client_turn_id: data.clientTurnId,
      text: data.text ?? null,
      image_path: data.imagePath ?? null,
      video_path: data.videoPath ?? null,
    }),
  decideCampaignChatCard: (
    sessionId: string,
    data: { clientTurnId: string; cardId: string; decision: "accept" | "reject" | "change"; text?: string }
  ) =>
    apiRequest<ChatSessionResponse>(`/api/v1/campaigns/chat/sessions/${sessionId}/decision`, "POST", {
      client_turn_id: data.clientTurnId,
      card_id: data.cardId,
      decision: data.decision,
      text: data.text ?? null,
    }),
  advanceCampaignChat: (sessionId: string) =>
    apiRequest<ChatSessionResponse>(`/api/v1/campaigns/chat/sessions/${sessionId}/advance`, "POST"),
  getSyncLearningHistory: (limit: number = 10) =>
    apiRequest<SyncLearningSnapshot[]>(`/api/v1/campaigns/sync-learning-history?limit=${limit}`, "GET"),

  // --- AI USAGE (contador de tokens) ---
  getAiUsage: (days: number = 30, adAccountId?: string) => {
    const params = new URLSearchParams({ days: String(days) });
    if (adAccountId) params.append("ad_account_id", adAccountId);
    return apiRequest<AiUsage>(`/api/v1/ai-usage?${params.toString()}`, "GET");
  },

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
