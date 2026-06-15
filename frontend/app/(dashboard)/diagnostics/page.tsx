"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Diagnostics() {
  const [insights, setInsights] = useState<any[]>([]);
  const [briefUrl, setBriefUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [dismissingId, setDismissingId] = useState("");

  const fetchData = async () => {
    try {
      const data = await api.getInsights();
      setInsights(data);
      setBriefUrl(api.getBriefHtml());
    } catch (e) {
      console.error("Error fetching insights:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleDismiss = async (id: string) => {
    setDismissingId(id);
    try {
      await api.dismissInsight(id);
      setInsights(insights.filter(ins => ins.id !== id));
    } catch (e) {
      console.error("Error dismissing insight:", e);
    } finally {
      setDismissingId("");
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando diagnósticos...</div>;
  }

  return (
    <div style={{ height: "calc(100vh - 5rem)", display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Title */}
      <div>
        <h2 style={{ fontSize: "2rem", fontWeight: "700" }}>Análisis y Diagnósticos de la IA</h2>
        <p style={{ color: "var(--text-secondary)" }}>Master Brief Ejecutivo de optimización y clasificación de campañas.</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 2.8fr", gap: "2rem", flexGrow: 1, minHeight: 0 }}>
        {/* Sidebar Insights */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem", overflowY: "auto", paddingRight: "0.5rem" }}>
          <h3 style={{ fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: "0.5rem", color: "#fff" }}>
            Alertas Activas ({insights.length})
          </h3>
          {insights.length === 0 ? (
            <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No hay alertas individuales activas.</p>
          ) : (
            insights.map((ins) => (
              <div 
                key={ins.id}
                style={{
                  padding: "1rem",
                  borderRadius: "12px",
                  background: "var(--card-bg)",
                  border: `1px solid ${ins.level === "CRITICAL" ? "rgba(239, 68, 68, 0.2)" : "var(--border-color)"}`,
                  position: "relative"
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem", alignItems: "center" }}>
                  <span className={`badge ${ins.level === "CRITICAL" ? "badge-danger" : "badge-warning"}`} style={{ fontSize: "0.6rem" }}>
                    {ins.level}
                  </span>
                  <button
                    onClick={() => handleDismiss(ins.id)}
                    disabled={dismissingId === ins.id}
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--text-muted)",
                      cursor: "pointer",
                      fontSize: "0.8rem"
                    }}
                  >
                    {dismissingId === ins.id ? "Descartando..." : "✕ Descartar"}
                  </button>
                </div>
                <h4 style={{ fontSize: "0.95rem", fontWeight: "600", marginBottom: "0.25rem", color: "#fff" }}>{ins.title}</h4>
                <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: "1.4" }}>{ins.description}</p>
                {ins.suggested_action_type && (
                  <div style={{ fontSize: "0.75rem", color: "var(--accent-purple)", marginTop: "0.5rem", fontWeight: "600" }}>
                    ↳ Acción sugerida: {ins.suggested_action_type}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* Master Brief Frame */}
        <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
          <iframe 
            src={briefUrl}
            style={{
              width: "100%",
              height: "100%",
              border: "1px solid var(--border-color)",
              borderRadius: "16px",
              background: "#0d0e12",
              boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.4)"
            }}
            title="Master Brief Report"
          />
        </div>
      </div>
    </div>
  );
}
