import { getApiUrl } from "./config";

// Interfaz para solicitudes HTTP genéricas
async function apiRequest<T = any>(
  endpoint: string,
  method: string = "GET",
  body?: any,
  customHeaders: Record<string, string> = {}
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

  // Si estamos en un subdominio, inyectamos el tenant slug para que el backend lo registre
  if (typeof window !== "undefined") {
    const hostname = window.location.hostname;
    if (hostname.includes(".") && !hostname.startsWith("www.") && !hostname.startsWith("api.")) {
      const parts = hostname.split(".");
      // Para cliente1.localhost o cliente1.lrojas.dev
      const slug = parts[0];
      headers["X-Tenant-Slug"] = slug;
    }
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
    
    if (response.status === 401 && typeof window !== "undefined") {
      // Token expirado, limpiar y redirigir
      localStorage.removeItem("token");
      localStorage.removeItem("user");
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
        errorMessage = errorJson.detail || errorMessage;
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

export const api = {
  // --- AUTH ---
  login: (data: any) => apiRequest("/api/v1/auth/login", "POST", data),
  signup: (data: any) => apiRequest("/api/v1/auth/signup", "POST", data),
  getMe: () => apiRequest("/api/v1/auth/me", "GET"),

  // --- ORGANIZATIONS & ACCOUNTS ---
  getOrg: () => apiRequest("/api/v1/organizations/my", "GET"),
  updateOrg: (data: any) => apiRequest("/api/v1/organizations/my", "PUT", data),
  getAdAccounts: () => apiRequest("/api/v1/organizations/my/ad-accounts", "GET"),
  connectAdAccount: (data: any) => apiRequest("/api/v1/organizations/my/ad-accounts", "POST", data),

  // --- CAMPAIGNS ---
  getRecentCampaigns: () => apiRequest("/api/v1/campaigns/recent", "GET"),
  getCampaignHistory: (campaignId: string) => apiRequest(`/api/v1/campaigns/${campaignId}/history`, "GET"),
  triggerSync: () => apiRequest("/api/v1/campaigns/sync-trigger", "POST"),

  // --- INSIGHTS ---
  getInsights: () => apiRequest("/api/v1/insights", "GET"),
  dismissInsight: (insightId: string) => apiRequest(`/api/v1/insights/${insightId}/dismiss`, "POST"),
  triggerAnalysis: () => apiRequest("/api/v1/insights/analyze-trigger", "POST"),

  // --- ACTIONS ---
  getPendingActions: () => apiRequest("/api/v1/actions", "GET"),
  getActionHistory: () => apiRequest("/api/v1/actions/history", "GET"),
  approveAction: (actionId: string, approve: boolean, rejectReason?: string) => 
    apiRequest(`/api/v1/actions/${actionId}/approve`, "POST", { approve, reject_reason: rejectReason }),
  getActionStatus: (actionId: string) => apiRequest(`/api/v1/actions/${actionId}/status`, "GET"),

  // --- REPORTS ---
  getBriefData: () => apiRequest("/api/v1/reports/brief-data", "GET")
};
