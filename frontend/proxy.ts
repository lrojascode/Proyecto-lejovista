import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  const url = request.nextUrl.clone();
  const hostname = request.headers.get("host") || "";

  // Excluir archivos estáticos, api interna y favicon
  if (
    url.pathname.startsWith("/_next") ||
    url.pathname.startsWith("/static") ||
    url.pathname.startsWith("/api") ||
    url.pathname.includes(".")
  ) {
    return NextResponse.next();
  }

  // Extraer el subdominio
  let subdomain = "";
  
  // En local hostname puede ser "cliente1.localhost:3000" o "localhost:3000"
  // En prod puede ser "cliente1.lrojas.dev" o "lrojas.dev"
  const hostParts = hostname.split(":");
  const domain = hostParts[0];
  const parts = domain.split(".");

  // Comprobar si hay un subdominio (excluyendo "www" y "api")
  if (parts.length > 1) {
    // Si es cliente1.localhost o cliente1.lrojas.dev
    const firstPart = parts[0];
    if (firstPart !== "www" && firstPart !== "api" && firstPart !== "localhost") {
      subdomain = firstPart;
    }
  }

  // Agregar el subdominio a las cabeceras de la petición para que esté disponible en Server Components
  const requestHeaders = new Headers(request.headers);
  if (subdomain) {
    requestHeaders.set("x-tenant-slug", subdomain);
  }

  // Si estamos en la raíz (sin subdominio) y no estamos logueados, podríamos mostrar el selector de inquilino
  // Pero permitimos que Next.js renderice la ruta correspondiente
  return NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });
}

export const proxyConfig = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
