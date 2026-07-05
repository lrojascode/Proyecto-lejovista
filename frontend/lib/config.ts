// Configuración de Frontend para LejoVista

export const BASE_DOMAIN = process.env.NEXT_PUBLIC_BASE_DOMAIN || "localhost:3000";

// URL base de la API. En local se comunica con el contenedor Nginx o directo al backend
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Modo "un solo hostname": para exponer la app detrás de un túnel (ngrok/
// Cloudflare) que solo da un hostname, sin el esquema de dos subdominios
// (<tenant>.dominio + api.dominio). Nginx rutea /api y /ws al backend bajo
// ese mismo hostname (ver nginx/conf.d/tunnel.conf), así que el frontend solo
// necesita pedir todo a origen relativo.
export const SINGLE_HOST_MODE = process.env.NEXT_PUBLIC_SINGLE_HOST_MODE === "true";

export const getApiUrl = (): string => {
  if (typeof window !== "undefined") {
    if (SINGLE_HOST_MODE) {
      return ""; // mismo origen: fetch("/api/v1/...") relativo a la página actual
    }
    // Si la API corre en el mismo host
    if (window.location.hostname.endsWith("localhost")) {
      return "http://localhost:8000";
    }
    return `https://api.${BASE_DOMAIN.replace(":3000", "")}`;
  }
  return API_URL;
};

// Slug de tenant a partir del subdominio, solo si el hostname actual
// realmente termina en el dominio base configurado (BASE_DOMAIN). Antes se
// activaba con "cualquier hostname que tenga un punto", lo que rompía en
// cualquier otro host (ej. *.ngrok-free.app) enviando un X-Tenant-Slug falso
// y provocando 403 en cada request.
export function getTenantSlugFromHostname(hostname: string): string | null {
  if (SINGLE_HOST_MODE) return null;
  const baseHost = BASE_DOMAIN.split(":")[0]; // BASE_DOMAIN puede traer puerto (ej. "localhost:3000")
  if (!baseHost || hostname === baseHost) return null;
  if (!hostname.endsWith(`.${baseHost}`)) return null;
  const subdomain = hostname.slice(0, -(`.${baseHost}`.length));
  if (!subdomain || subdomain === "www" || subdomain === "api") return null;
  return subdomain;
}
