"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { safeRedirect } from "@/lib/navigation";
import { getTenantSlugFromHostname } from "@/lib/config";
import { api, hasAccessToken } from "@/lib/api";
import { WebSocketProvider } from "@/components/providers/WebSocketProvider";
import { AdAccountProvider, useAdAccount } from "@/components/providers/AdAccountProvider";
import {
  GridIcon, TargetIcon, DocIcon, SparkleIcon, BoltIcon, GearIcon, MenuIcon, CloseIcon, GaugeIcon,
} from "@/components/icons";

// Selector global de cuenta publicitaria. Oculto si la organización tiene 0 o 1
// cuenta (caso común hoy) — solo se muestra cuando hay que elegir de verdad.
function AdAccountSelector() {
  const { accounts, activeAdAccountId, setActiveAdAccountId } = useAdAccount();

  if (accounts.length <= 1) return null;

  return (
    <div style={{ marginTop: "8px" }}>
      <select
        className="form-input"
        style={{ background: "var(--color-obsidian)", fontSize: "12px", padding: "6px 8px" }}
        value={activeAdAccountId || ""}
        onChange={(e) => {
          setActiveAdAccountId(e.target.value);
          window.location.reload();
        }}
      >
        {!activeAdAccountId && (
          <option value="" disabled>Selecciona una cuenta</option>
        )}
        {accounts.map((acc) => (
          <option key={acc.id} value={acc.id}>
            {acc.name} ({acc.external_account_id})
          </option>
        ))}
      </select>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [userName, setUserName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [orgId, setOrgId] = useState("");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    // Cerrar barra lateral al cambiar de ruta
    setIsSidebarOpen(false);
  }, [pathname]);

  useEffect(() => {
    // (S-2) El access token vive en memoria (lib/api.ts) y no sobrevive a un
    // reload por su cuenta. Si ya lo tenemos (venimos de login/signup en esta
    // misma pestaña, sin reload de por medio) basta con confirmarlo contra
    // /auth/me — forzar un refresh aquí sería redundante y, peor, descartaría
    // una sesión recién obtenida si la cookie de refresh no acompañara la
    // request (p. ej. cruzando de un subdominio de tenant a otro host de API
    // en desarrollo local). Sin token en memoria (reload real / pestaña
    // nueva), sí hace falta renovar desde la cookie httpOnly del refresh.
    let cancelled = false;
    const afterAuth = (user: { full_name: string; organization_id?: string | null } | null) => {
      if (cancelled) return;
      if (!user) {
        safeRedirect("/login");
        return;
      }
      setUserName(user.full_name);
      setOrgId(user.organization_id || "");
      // Capturar subdominio (solo si es un subdominio real de BASE_DOMAIN;
      // en modo single-host o cualquier otro host esto da null y se usa
      // el nombre de fallback, puramente cosmético).
      setOrgSlug(getTenantSlugFromHostname(window.location.hostname) || "cliente1");
      setLoading(false);
    };

    if (hasAccessToken()) {
      api.getMe().then(afterAuth).catch(() => afterAuth(null));
    } else {
      api.bootstrapSession().then(afterAuth);
    }
    return () => { cancelled = true; };
  }, []);

  const handleLogout = () => {
    // Best-effort: revoca el refresh en el backend y limpia la sesión local
    // (access token en memoria + user) sin bloquear el logout.
    api.logout().catch(() => {}).finally(() => {
      localStorage.removeItem("active_ad_account_id");
      safeRedirect("/login");
    });
  };

  if (loading) {
    return (
      <div style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "1.2rem",
        color: "var(--text-secondary)"
      }}>
        Cargando LejoVista...
      </div>
    );
  }

  const menuItems = [
    { name: "Resumen", path: "/", icon: GridIcon },
    { name: "Campañas", path: "/campaigns", icon: TargetIcon },
    { name: "Reporte", path: "/reporte", icon: DocIcon },
    { name: "Diagnósticos IA", path: "/diagnostics", icon: SparkleIcon },
    { name: "Acciones Sugeridas", path: "/actions", icon: BoltIcon },
    { name: "Uso de IA", path: "/ai-usage", icon: GaugeIcon },
    { name: "Configuración", path: "/settings", icon: GearIcon },
  ];

  return (
    <AdAccountProvider orgId={orgId}>
      <div className="dashboard-wrapper">
        {/* Botón de Menú Móvil */}
        <button
          className="menu-toggle-btn"
          onClick={() => setIsSidebarOpen(!isSidebarOpen)}
          aria-label="Toggle Menu"
        >
          {isSidebarOpen ? <CloseIcon /> : <MenuIcon />}
        </button>

        {/* Sidebar */}
        <aside className={`sidebar ${isSidebarOpen ? "open" : ""}`}>
          <div>
            <div style={{ marginBottom: "32px", padding: "0 10px" }}>
              <h1 style={{ fontSize: "18px", fontWeight: 590, color: "var(--color-paper)", letterSpacing: "-0.011em" }}>
                LejoVista
              </h1>
              <span className="mono" style={{ color: "var(--color-ash)" }}>
                tenant: {orgSlug}
              </span>
              <AdAccountSelector />
            </div>

            <nav style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              {menuItems.map((item) => {
                const isActive = pathname === item.path;
                const Icon = item.icon;
                return (
                  <Link
                    key={item.path}
                    href={item.path}
                    className={`nav-item ${isActive ? "active" : ""}`}
                  >
                    <Icon />
                    {item.name}
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* User Info & Logout */}
          <div style={{
            borderTop: "1px solid var(--color-graphite)",
            paddingTop: "16px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
            padding: "16px 10px 0 10px"
          }}>
            <div>
              <p style={{ fontSize: "13px", fontWeight: 510, color: "var(--color-paper)" }}>{userName}</p>
              <p style={{ fontSize: "12px", color: "var(--color-ash)" }}>Administrador</p>
            </div>
            <button
              onClick={handleLogout}
              className="btn-secondary btn-sm"
              style={{ width: "100%" }}
            >
              Cerrar Sesión
            </button>
          </div>
        </aside>

        {/* Main Content Container */}
        <main className="main-content">
          <WebSocketProvider tenantId={orgId}>
            {children}
          </WebSocketProvider>
        </main>
      </div>
    </AdAccountProvider>
  );
}
