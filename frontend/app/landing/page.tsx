"use client";

import { useState } from "react";

export default function Home() {
  const [slug, setSlug] = useState("cliente1");

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", position: "relative", overflow: "hidden" }}>
      {/* Gradient floor atmosférico — el único gradiente permitido del sistema */}
      <div aria-hidden style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: "-40vh",
        height: "70vh",
        background: "radial-gradient(ellipse 80% 100% at 50% 100%, rgba(99, 102, 241, 0.12) 0%, rgba(2, 184, 204, 0.05) 45%, transparent 75%)",
        pointerEvents: "none"
      }} />

      {/* Nav bar */}
      <header style={{
        width: "100%",
        maxWidth: "1200px",
        margin: "0 auto",
        padding: "16px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }}>
        <span style={{ fontSize: "16px", fontWeight: 510, color: "var(--color-paper)", letterSpacing: "-0.011em" }}>
          LejoVista
        </span>
        <nav style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <a
            href={`http://${slug}.localhost:3000/login`}
            style={{ color: "var(--color-mist)", fontSize: "13px", padding: "8px 12px" }}
          >
            Iniciar sesión
          </a>
          <a
            href={`http://${slug}.localhost:3000/login`}
            style={{
              background: "var(--color-paper)",
              color: "var(--color-void)",
              borderRadius: "9999px",
              padding: "8px 16px",
              fontSize: "13px",
              fontWeight: 510
            }}
          >
            Ir al panel
          </a>
        </nav>
      </header>

      {/* Hero */}
      <main style={{
        flexGrow: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px 96px 24px",
        textAlign: "center",
        position: "relative"
      }}>
        <h1 style={{
          fontSize: "clamp(40px, 7vw, 64px)",
          lineHeight: 1,
          fontWeight: 590,
          letterSpacing: "-0.022em",
          color: "var(--color-paper)",
          maxWidth: "760px"
        }}>
          LejoVista Platform
        </h1>
        <p style={{
          color: "var(--color-mist)",
          fontSize: "18px",
          lineHeight: 1.5,
          maxWidth: "520px",
          marginTop: "20px"
        }}>
          Gestión Autónoma, Análisis y Optimización de Campañas con Inteligencia Artificial.
        </p>

        {/* Card funcional de acceso por subdominio (multi-tenant) */}
        <div className="glass-card" style={{ maxWidth: "520px", width: "100%", marginTop: "48px", textAlign: "left" }}>
          <h3 style={{ fontSize: "15px", fontWeight: 510, marginBottom: "8px" }}>
            Pruebas en Entorno Local
          </h3>
          <p style={{ fontSize: "14px", color: "var(--color-fog)", lineHeight: 1.6, marginBottom: "16px" }}>
            El sistema utiliza subdominios locales para identificar a cada inquilino (SaaS Multi-tenant).
            Usa el botón de abajo para ir al panel de pruebas.
          </p>

          <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "16px" }}>
            <span style={{ fontSize: "13px", color: "var(--color-fog)", whiteSpace: "nowrap" }}>
              Subdominio:
            </span>
            <input
              type="text"
              className="form-input"
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
              placeholder="nombre-cliente"
            />
            <span className="mono" style={{ color: "var(--color-fog)" }}>
              .localhost:3000
            </span>
          </div>

          <a
            href={`http://${slug}.localhost:3000/login`}
            className="btn-primary"
            style={{ width: "100%" }}
          >
            Ir al Dashboard de {slug}
          </a>

          <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "16px" }}>
            Para producción, configura el dominio base (ej. <code>lrojas.dev</code>) en tu archivo <code>.env</code>.
          </div>
        </div>
      </main>
    </div>
  );
}
