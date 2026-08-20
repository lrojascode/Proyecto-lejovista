"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { APP_NAME, getTenantSlugFromHostname } from "@/lib/config";

import type { TenantBrand } from "@/lib/types";

export default function Login() {
  const router = useRouter();
  const [tenantSlug, setTenantSlug] = useState("");
  const [brand, setBrand] = useState<TenantBrand | null>(null);
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Detectar el tenant slug del subdominio en el navegador y cargar marca
    if (typeof window !== "undefined") {
      const slug = getTenantSlugFromHostname(window.location.hostname);
      if (slug) {
        setTenantSlug(slug);
        setOrgName(slug.charAt(0).toUpperCase() + slug.slice(1));
        api.getTenantBrand(slug).then(setBrand).catch(() => {});
      } else {
        setTenantSlug("");
        setOrgName("");
        api.getTenantBrand().then(setBrand).catch(() => {});
      }
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      // login/signup guardan la sesión ellos mismos (S-2: access token en
      // memoria, refresh token como cookie httpOnly que pone el backend).
      if (isLogin) {
        await api.login({ email, password });
      } else {
        await api.signup({
          email,
          password,
          full_name: fullName,
          organization_slug: tenantSlug || "default",
          organization_name: orgName || "Mi Organización",
        });
      }

      // (S-2) Navegación del lado del cliente, NO window.location: el access
      // token que acaba de guardar api.login/signup vive en memoria (lib/api.ts)
      // y una recarga completa lo perdería, obligando a un refresh contra la
      // cookie httpOnly justo en el peor momento (recién logueado). El router
      // de Next preserva el módulo JS entre esta página y el dashboard.
      router.push("/");
    } catch (err: any) {
      setError(err.message || "Credenciales incorrectas.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "1.5rem"
    }}>
      <div className="glass-card" style={{ maxWidth: "420px", width: "100%" }}>
        <div style={{ textAlign: "center", marginBottom: "2rem" }}>
          {brand?.logo_url ? (
            <div style={{ display: "flex", justifyContent: "center", marginBottom: "12px" }}>
              <img
                src={brand.logo_url}
                alt={brand.display_name || brand.name}
                style={{ maxHeight: "48px", maxWidth: "180px", objectFit: "contain" }}
              />
            </div>
          ) : null}
          <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em", marginBottom: "0.25rem" }}>
            {brand?.display_name || brand?.name || APP_NAME}
          </h2>
          {tenantSlug ? (
            <p style={{ color: "var(--color-fog)", fontSize: "14px" }}>
              Accediendo al espacio de <strong style={{ color: "var(--color-paper)", fontWeight: 510 }}>{brand?.display_name || brand?.name || tenantSlug}</strong>
            </p>
          ) : (
            <p style={{ color: "var(--color-fog)", fontSize: "14px" }}>
              Plataforma de Optimización de Meta Ads
            </p>
          )}
        </div>

        {error && (
          <div style={{
            background: "rgba(235, 87, 87, 0.1)",
            border: "1px solid rgba(235, 87, 87, 0.2)",
            color: "var(--color-coral-red)",
            borderRadius: "6px",
            padding: "0.75rem 1rem",
            fontSize: "14px",
            marginBottom: "1.5rem"
          }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
          {!isLogin && (
            <>
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Nombre Completo
                </label>
                <input
                  type="text"
                  className="form-input"
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Alejandro Rojas"
                />
              </div>

              <div>
                <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Nombre de la Empresa
                </label>
                <input
                  type="text"
                  className="form-input"
                  required
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  placeholder="Cables MTV S.A.C."
                />
              </div>
            </>
          )}

          <div>
            <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
              Correo Electrónico
            </label>
            <input
              type="email"
              className="form-input"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="correo@empresa.com"
            />
          </div>

          <div>
            <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
              Contraseña
            </label>
            <input
              type="password"
              className="form-input"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </div>

          <button type="submit" className="btn-primary" style={{ marginTop: "0.5rem" }} disabled={loading}>
            {loading ? "Procesando..." : isLogin ? "Iniciar Sesión" : "Crear Cuenta"}
          </button>
        </form>

        <div style={{ textAlign: "center", marginTop: "1.5rem", fontSize: "0.9rem" }}>
          <button 
            type="button" 
            onClick={() => setIsLogin(!isLogin)} 
            style={{
              background: "none",
              border: "none",
              color: "var(--color-fog)",
              cursor: "pointer",
              fontFamily: "inherit",
              fontSize: "13px",
              textDecoration: "underline",
              textUnderlineOffset: "3px"
            }}
          >
            {isLogin ? "¿No tienes cuenta? Regístrate aquí" : "¿Ya tienes cuenta? Inicia sesión"}
          </button>
        </div>
      </div>
    </div>
  );
}
