/** Mapa pantalla → módulo de la vista del cliente.
 *
 * Las claves tienen que coincidir con AVAILABLE_MODULES del backend
 * (app/tenant_rules/modules.py). El catálogo canónico es el del backend —este
 * archivo solo sabe qué ruta corresponde a qué módulo, que es lo que el
 * backend no puede saber.
 */
export const MODULE_ROUTES: { path: string; module: string }[] = [
  { path: "/", module: "resumen" },
  { path: "/campaigns", module: "campanas" },
  { path: "/chat", module: "chat" },
  { path: "/reporte", module: "reporte" },
  { path: "/diagnostics", module: "diagnosticos" },
  { path: "/actions", module: "acciones" },
  { path: "/ai-usage", module: "ai_usage" },
  { path: "/settings", module: "settings" },
];

/** Módulo al que pertenece una ruta, o null si no es una pantalla del menú.
 *
 * Se compara por prefijo para que las subrutas (`/campaigns/123`,
 * `/campaigns/new`) caigan en su pantalla. La raíz se compara exacta y aparte:
 * con prefijo, `"/"` matchearía TODA ruta y el Resumen se comería el menú
 * entero.
 *
 * El prefijo exige la barra (`/campaigns/`) para que una ruta futura como
 * `/campaigns-archivadas` no quede clasificada como Campañas por accidente.
 */
export function moduleForPath(pathname: string): string | null {
  if (pathname === "/") return "resumen";
  const match = MODULE_ROUTES.filter((r) => r.path !== "/").find(
    (r) => pathname === r.path || pathname.startsWith(r.path + "/")
  );
  return match ? match.module : null;
}
