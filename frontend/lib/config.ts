// Configuración de Frontend para LejoVista

export const BASE_DOMAIN = process.env.NEXT_PUBLIC_BASE_DOMAIN || "localhost:3000";

// URL base de la API. En local se comunica con el contenedor Nginx o directo al backend
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const getApiUrl = (): string => {
  // Si estamos en el navegador y el host es api.localhost, usamos la url absoluta
  if (typeof window !== "undefined") {
    // Si la API corre en el mismo host
    if (window.location.hostname.endsWith("localhost")) {
      return "http://localhost:8000";
    }
    return `https://api.${BASE_DOMAIN.replace(":3000", "")}`;
  }
  return API_URL;
};
