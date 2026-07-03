"use client";

import { useState } from "react";
import Link from "next/link";

export default function Home() {
  const [slug, setSlug] = useState("cliente1");

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexDirection: "column",
      padding: "2rem",
      textAlign: "center"
    }}>
      <div className="glass-card glow-purple" style={{ maxWidth: "550px", width: "100%" }}>
        <h1 style={{ fontSize: "2.5rem", marginBottom: "1rem", fontWeight: "800" }}>
          LejoVista Platform
        </h1>
        <p style={{ color: "var(--text-secondary)", marginBottom: "2rem" }}>
          Gestión Autónoma, Análisis y Optimización de Campañas con Inteligencia Artificial.
        </p>

        <div style={{
          background: "rgba(255,255,255,0.02)",
          border: "1px solid var(--border-color)",
          borderRadius: "12px",
          padding: "1.5rem",
          marginBottom: "2rem",
          textAlign: "left"
        }}>
          <h3 style={{ fontSize: "1.1rem", marginBottom: "0.75rem", color: "#fff" }}>
            🚀 Pruebas en Entorno Local
          </h3>
          <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "1rem" }}>
            El sistema utiliza subdominios locales para identificar a cada inquilino (SaaS Multi-tenant).
            Usa el botón de abajo para ir al panel de pruebas.
          </p>

          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "1rem" }}>
            <span style={{ fontSize: "0.95rem", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
              Subdominio:
            </span>
            <input
              type="text"
              className="form-input"
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
              placeholder="nombre-cliente"
              style={{ padding: "0.5rem 0.75rem" }}
            />
            <span style={{ fontSize: "0.95rem", color: "var(--text-secondary)" }}>
              .localhost:3000
            </span>
          </div>

          <a 
            href={`http://${slug}.localhost:3000/login`}
            className="btn-primary"
            style={{ width: "100%", display: "flex", textDecoration: "none" }}
          >
            Ir al Dashboard de {slug}
          </a>
        </div>

        <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
          Para producción, configura el dominio base (ej. <code>lrojas.dev</code>) en tu archivo <code>.env</code>.
        </div>
      </div>
    </div>
  );
}
