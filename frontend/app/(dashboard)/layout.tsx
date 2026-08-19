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

import type { TenantBrand } from "@/lib/types";

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
          // Cambiar de cuenta ya no recarga toda la página: setActiveAdAccountId
          // actualiza el contexto + localStorage, y AccountScopedContent
          // remonta el contenido para re-fetchear con el nuevo X-Ad-Account-Id.
          setActiveAdAccountId(e.target.value);
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

function TokenWarningBanner() {
  const { accounts, activeAdAccountId } = useAdAccount();
  const activeAccount = accounts.find((a) => a.id === activeAdAccountId);

  if (!activeAccount) return null;

  const isExpired = activeAccount.status === "EXPIRED" || activeAccount.last_sync_error_code === 190;
  const isExpiringSoon = activeAccount.token_warning || (
    activeAccount.token_expires_in_days !== null &&
    activeAccount.token_expires_in_days !== undefined &&
    activeAccount.token_expires_in_days <= 7
  );

  if (!isExpired && !isExpiringSoon) return null;

  const days = activeAccount.token_expires_in_days ?? 0;

  return (
    <div
      style={{
        background: isExpired ? "rgba(239, 68, 68, 0.12)" : "rgba(245, 158, 11, 0.12)",
        border: `1px solid ${isExpired ? "rgba(239, 68, 68, 0.35)" : "rgba(245, 158, 11, 0.35)"}`,
        color: isExpired ? "#fca5a5" : "#fcd34d",
        borderRadius: "8px",
        padding: "12px 16px",
        marginBottom: "1.5rem",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "1rem",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "13.5px" }}>
        <span style={{ fontSize: "18px" }}>{isExpired ? "🚫" : "⚠️"}</span>
        <span>
          {isExpired ? (
            <>
              <strong>Token de Meta Expirado:</strong> El token de acceso para la cuenta <strong>{activeAccount.name}</strong> ha expirado. La sincronización y optimización automática están suspendidas.
            </>
          ) : (
            <>
              <strong>Aviso anticipado de expiración:</strong> El token de acceso de Meta para <strong>{activeAccount.name}</strong> vencerá en <strong>{days} {days === 1 ? "día" : "días"}</strong>. Reconéctalo antes de que venza para evitar interrupciones.
            </>
          )}
        </span>
      </div>
      <Link
        href="/settings"
        style={{
          background: isExpired ? "#ef4444" : "#f59e0b",
          color: "#000",
          fontWeight: 600,
          fontSize: "12px",
          padding: "6px 12px",
          borderRadius: "6px",
          textDecoration: "none",
          whiteSpace: "nowrap",
        }}
      >
        Reconectar en Configuración
      </Link>
    </div>
  );
}

// Remonta el contenido de la página cuando cambia la cuenta publicitaria activa,
// para que cada página vuelva a hacer sus fetches con el nuevo header
// X-Ad-Account-Id (lo lee apiRequest de localStorage). Reemplaza el
// window.location.reload() que hacía el selector — sin recargar todo el
// navegador ni reconectar el WebSocket. Va DENTRO de WebSocketProvider para no
// tumbar la conexión al cambiar de cuenta.
function AccountScopedContent({ children }: { children: React.ReactNode }) {
  const { activeAdAccountId } = useAdAccount();
  return (
    <div key={activeAdAccountId || "none"} style={{ display: "contents" }}>
      <TokenWarningBanner />
      {children}
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [userName, setUserName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [orgId, setOrgId] = useState("");
  const [brand, setBrand] = useState<TenantBrand | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    // Cerrar barra lateral al cambiar de ruta
    setIsSidebarOpen(false);
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;
    const afterAuth = (user: { full_name: string; organization_id?: string | null } | null) => {
      if (cancelled) return;
      if (!user) {
        safeRedirect("/login");
        return;
      }
      setUserName(user.full_name);
      setOrgId(user.organization_id || "");
      const detectedSlug = getTenantSlugFromHostname(window.location.hostname) || "";
      setOrgSlug(detectedSlug || "cliente1");
      api.getTenantBrand(detectedSlug).then((b) => {
        if (!cancelled) setBrand(b);
      }).catch(() => {});
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
    { name: "Chat Analista", path: "/chat", icon: SparkleIcon },
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
              {brand?.logo_url ? (
                <div style={{ marginBottom: "8px" }}>
                  <img
                    src={brand.logo_url}
                    alt={brand.display_name || brand.name}
                    style={{ maxHeight: "32px", maxWidth: "160px", objectFit: "contain" }}
                  />
                </div>
              ) : null}
              <h1 style={{ fontSize: "18px", fontWeight: 590, color: "var(--color-paper)", letterSpacing: "-0.011em" }}>
                {brand?.display_name || brand?.name || "LejoVista"}
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
            <AccountScopedContent>{children}</AccountScopedContent>
          </WebSocketProvider>
        </main>
      </div>
    </AdAccountProvider>
  );
}
