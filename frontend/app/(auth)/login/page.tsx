"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { safeRedirect } from "@/lib/navigation";

export default function Login() {
  const [tenantSlug, setTenantSlug] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Detectar el tenant slug del subdominio en el navegador
    if (typeof window !== "undefined") {
      const hostname = window.location.hostname;
      const parts = hostname.split(".");
      if (parts.length > 1 && parts[0] !== "www" && parts[0] !== "api" && parts[0] !== "localhost") {
        setTenantSlug(parts[0]);
        setOrgName(parts[0].charAt(0).toUpperCase() + parts[0].slice(1) + " Corp");
      } else {
        setTenantSlug("cliente1");
        setOrgName("Cliente 1 Corp");
      }
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      let response;
      if (isLogin) {
        response = await api.login({ email, password });
      } else {
        response = await api.signup({
          email,
          password,
          full_name: fullName,
          organization_slug: tenantSlug,
          organization_name: orgName,
        });
      }

      // Guardar sesión
      localStorage.setItem("token", response.access_token);
      if (response.refresh_token) localStorage.setItem("refresh_token", response.refresh_token);
      localStorage.setItem("user", JSON.stringify(response.user));
      
      // Redirigir al dashboard principal del tenant
      safeRedirect("/");
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
          <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em", marginBottom: "0.25rem" }}>
            LejoVista
          </h2>
          <p style={{ color: "var(--color-fog)", fontSize: "14px" }}>
            Acceso para <strong style={{ color: "var(--color-paper)", fontWeight: 510 }}>{tenantSlug || "Inquilino"}</strong>
          </p>
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
