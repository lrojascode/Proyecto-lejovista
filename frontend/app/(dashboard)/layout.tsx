"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { safeRedirect } from "@/lib/navigation";
import { api } from "@/lib/api";
import { WebSocketProvider } from "@/components/providers/WebSocketProvider";

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

      // Capturar subdominio
      const hostParts = window.location.hostname.split(".");
      if (hostParts.length > 1 && hostParts[0] !== "localhost" && hostParts[0] !== "www" && hostParts[0] !== "api") {
        setOrgSlug(hostParts[0]);
      } else {
        setOrgSlug("cliente1");
      }
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
    { name: "📊 Resumen", path: "/" },
    { name: "🎯 Campañas", path: "/campaigns" },
    { name: "📄 Reporte", path: "/reporte" },
    { name: "🧠 Diagnósticos IA", path: "/diagnostics" },
    { name: "🤖 Acciones Sugeridas", path: "/actions" },
    { name: "⚙️ Configuración", path: "/settings" },
  ];

  return (
    <div className="dashboard-wrapper">
      {/* Botón de Menú Móvil */}
      <button 
        className="menu-toggle-btn"
        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
        aria-label="Toggle Menu"
      >
        {isSidebarOpen ? "✕" : "☰"}
      </button>

      {/* Sidebar */}
      <aside className={`sidebar ${isSidebarOpen ? "open" : ""}`}>
        <div>
          <div style={{ marginBottom: "2.5rem" }}>
            <h1 style={{ fontSize: "1.8rem", fontWeight: "800", background: "linear-gradient(135deg, #a78bfa 0%, #60a5fa 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              LejoVista
            </h1>
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Tenant: {orgSlug}
            </span>
          </div>

          <nav style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {menuItems.map((item) => {
              const isActive = pathname === item.path;
              return (
                <Link
                  key={item.path}
                  href={item.path}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "0.85rem 1rem",
                    borderRadius: "10px",
                    fontSize: "0.95rem",
                    fontWeight: isActive ? "600" : "400",
                    color: isActive ? "#ffffff" : "var(--text-secondary)",
                    background: isActive ? "rgba(139, 92, 246, 0.15)" : "transparent",
                    border: isActive ? "1px solid rgba(139, 92, 246, 0.25)" : "1px solid transparent",
                    transition: "all var(--transition-fast)"
                  }}
                >
                  {item.name}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* User Info & Logout */}
        <div style={{
          borderTop: "1px solid var(--border-color)",
          paddingTop: "1.5rem",
          display: "flex",
          flexDirection: "column",
          gap: "1rem"
        }}>
          <div>
            <p style={{ fontSize: "0.9rem", fontWeight: "600", color: "#fff" }}>{userName}</p>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Administrador</p>
          </div>
          <button
            onClick={handleLogout}
            className="btn-secondary"
            style={{ width: "100%", padding: "0.6rem 1rem", fontSize: "0.85rem" }}
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
  );
}
