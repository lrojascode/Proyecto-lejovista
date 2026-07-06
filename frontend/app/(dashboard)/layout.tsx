"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { safeRedirect } from "@/lib/navigation";
import { getTenantSlugFromHostname } from "@/lib/config";
import { api } from "@/lib/api";
import { WebSocketProvider } from "@/components/providers/WebSocketProvider";
import { AdAccountProvider, useAdAccount } from "@/components/providers/AdAccountProvider";
import {
  GridIcon, TargetIcon, DocIcon, SparkleIcon, BoltIcon, GearIcon, MenuIcon, CloseIcon,
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
    // Verificar autenticación local
    const token = localStorage.getItem("token");
    const userStr = localStorage.getItem("user");

    if (!token || !userStr) {
      safeRedirect("/login");
      return;
    }

    try {
      const user = JSON.parse(userStr);
      setUserName(user.full_name);
      setOrgId(user.organization_id || "");

      // Capturar subdominio (solo si es un subdominio real de BASE_DOMAIN;
      // en modo single-host o cualquier otro host esto da null y se usa
      // el nombre de fallback, puramente cosmético).
      setOrgSlug(getTenantSlugFromHostname(window.location.hostname) || "cliente1");
    } catch (e) {
      safeRedirect("/login");
      return;
    }

    setLoading(false);
  }, []);

  const handleLogout = () => {
    // Revocar el refresh token en el backend (S-9), sin bloquear el logout local.
    const refreshToken = localStorage.getItem("refresh_token");
    if (refreshToken) {
      api.logout(refreshToken).catch(() => {});
    }
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("active_ad_account_id");
    safeRedirect("/login");
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
