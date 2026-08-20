"use client";

import { useState } from "react";
import Link from "next/link";
import { APP_NAME } from "@/lib/config";

export default function Home() {
  const [slug, setSlug] = useState("cliente1");

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", position: "relative", overflow: "hidden", background: "var(--color-obsidian)" }}>
      {/* Gradient floor atmosférico */}
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
        padding: "20px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottom: "1px solid var(--border-subtle)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", letterSpacing: "-0.012em" }}>
            {APP_NAME}
          </span>
          <span style={{
            fontSize: "11px",
            fontWeight: 600,
            padding: "2px 8px",
            borderRadius: "9999px",
            background: "rgba(99, 102, 241, 0.15)",
            color: "var(--color-signal-teal)",
            border: "1px solid rgba(99, 102, 241, 0.25)",
          }}>
            Meta Ads AI
          </span>
        </div>

        <nav style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <Link href="/login" className="btn-secondary btn-sm" style={{ textDecoration: "none" }}>
            Iniciar Sesión
          </Link>
          <a
            href={`http://${slug}.localhost:3000/login`}
            style={{
              background: "var(--color-paper)",
              color: "var(--color-void)",
              borderRadius: "9999px",
              padding: "8px 18px",
              fontSize: "13px",
              fontWeight: 590,
              textDecoration: "none",
            }}
          >
            Ir al Espacio
          </a>
        </nav>
      </header>

      {/* Hero */}
      <main style={{
        flexGrow: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "64px 24px 80px 24px",
        textAlign: "center",
        position: "relative",
        maxWidth: "1200px",
        margin: "0 auto",
        width: "100%",
      }}>
        <div style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "8px",
          padding: "6px 14px",
          borderRadius: "9999px",
          background: "rgba(255,255,255,0.03)",
          border: "1px solid var(--border-subtle)",
          fontSize: "13px",
          color: "var(--color-fog)",
          marginBottom: "24px",
        }}>
          <span>🛡️</span>
          <span>Optimización de Meta Ads con Gobernanza Estricta y Cero Alucinaciones</span>
        </div>

        <h1 style={{
          fontSize: "clamp(36px, 6vw, 58px)",
          lineHeight: 1.08,
          fontWeight: 590,
          letterSpacing: "-0.025em",
          color: "var(--color-paper)",
          maxWidth: "840px",
        }}>
          Gestión y Optimización Autónoma de Meta Ads con Inteligencia Artificial
        </h1>

        <p style={{
          color: "var(--color-fog)",
          fontSize: "18px",
          lineHeight: 1.6,
          maxWidth: "680px",
          marginTop: "20px",
        }}>
          {APP_NAME} combina análisis estadístico determinístico en Python con modelos de lenguaje avanzados para auditar, diagnosticar y crear campañas de Meta Ads bajo estrictos límites financieros y aprobación humana.
        </p>

        {/* 4 Pilares de Valor */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: "1.5rem",
          width: "100%",
          marginTop: "56px",
          textAlign: "left",
        }}>
          <div className="glass-card" style={{ padding: "1.5rem" }}>
            <div style={{ fontSize: "24px", marginBottom: "12px" }}>📊</div>
            <h3 style={{ fontSize: "16px", fontWeight: 590, color: "var(--color-paper)", marginBottom: "8px" }}>
              IA Determinística y Grounding
            </h3>
            <p style={{ fontSize: "13.5px", color: "var(--color-fog)", lineHeight: 1.5 }}>
              Regla AI-1: Ningún número o métrica es inventado por el LLM. Todos los KPIs, CPL, gastos y conversiones provienen directamente de Meta Graph API con tolerancia exacta.
            </p>
          </div>

          <div className="glass-card" style={{ padding: "1.5rem" }}>
            <div style={{ fontSize: "24px", marginBottom: "12px" }}>🔒</div>
            <h3 style={{ fontSize: "16px", fontWeight: 590, color: "var(--color-paper)", marginBottom: "8px" }}>
              Control Financiero y Creación Segura
            </h3>
            <p style={{ fontSize: "13.5px", color: "var(--color-fog)", lineHeight: 1.5 }}>
              Protección de Spend Cap por organización. Todas las campañas se crean siempre en estado <code>PAUSED</code> (pausado) para requerir aprobación humana explícita antes de activarse.
            </p>
          </div>

          <div className="glass-card" style={{ padding: "1.5rem" }}>
            <div style={{ fontSize: "24px", marginBottom: "12px" }}>💬</div>
            <h3 style={{ fontSize: "16px", fontWeight: 590, color: "var(--color-paper)", marginBottom: "8px" }}>
              Chat Dual (Analista vs Creador)
            </h3>
            <p style={{ fontSize: "13.5px", color: "var(--color-fog)", lineHeight: 1.5 }}>
              Dos experiencias especializadas: modo Analista para ejecutivos (consultas y comparaciones en lenguaje natural) y modo Creador para operadores de campañas.
            </p>
          </div>

          <div className="glass-card" style={{ padding: "1.5rem" }}>
            <div style={{ fontSize: "24px", marginBottom: "12px" }}>🏢</div>
            <h3 style={{ fontSize: "16px", fontWeight: 590, color: "var(--color-paper)", marginBottom: "8px" }}>
              Multi-Tenant y Marca Blanca
            </h3>
            <p style={{ fontSize: "13.5px", color: "var(--color-fog)", lineHeight: 1.5 }}>
              Aislamiento riguroso por organización (`organization_id` y subdominio). Personalización de logo, nombre comercial y colores corporativos por tenant.
            </p>
          </div>
        </div>

        {/* Acceso por Subdominio / Local */}
        <div className="glass-card" style={{ maxWidth: "560px", width: "100%", marginTop: "48px", textAlign: "left", padding: "1.75rem" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 590, color: "var(--color-paper)", marginBottom: "8px" }}>
            Acceder a un Espacio de Trabajo
          </h3>
          <p style={{ fontSize: "13.5px", color: "var(--color-fog)", lineHeight: 1.6, marginBottom: "16px" }}>
            Ingresa el subdominio de tu organización para acceder al panel seguro de tu empresa.
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

          <div style={{ display: "flex", gap: "12px" }}>
            <a
              href={`http://${slug}.localhost:3000/login`}
              className="btn-primary"
              style={{ flexGrow: 1, textAlign: "center", textDecoration: "none" }}
            >
              Ir al Dashboard de {slug}
            </a>
            <Link
              href="/login"
              className="btn-secondary"
              style={{ textDecoration: "none", whiteSpace: "nowrap" }}
            >
              Login Directo
            </Link>
          </div>
        </div>
      </main>

      {/* Footer Legal */}
      <footer style={{
        width: "100%",
        maxWidth: "1200px",
        margin: "0 auto",
        padding: "32px 24px",
        borderTop: "1px solid var(--border-subtle)",
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "space-between",
        alignItems: "center",
        fontSize: "13px",
        color: "var(--color-ash)",
        gap: "1rem",
      }}>
        <div>
          <strong style={{ color: "var(--color-paper)", fontWeight: 510 }}>{APP_NAME}</strong> — Optimización Autónoma de Meta Ads
          <div style={{ fontSize: "12px", marginTop: "4px" }}>© 2026 Todos los derechos reservados.</div>
        </div>
        <div style={{ display: "flex", gap: "20px" }}>
          <Link href="/terms" style={{ color: "var(--color-fog)", textDecoration: "none" }}>Términos de Servicio</Link>
          <Link href="/privacy" style={{ color: "var(--color-fog)", textDecoration: "none" }}>Política de Privacidad</Link>
          <Link href="/data-deletion" style={{ color: "var(--color-fog)", textDecoration: "none" }}>Eliminación de Datos</Link>
        </div>
      </footer>
    </div>
  );
}
