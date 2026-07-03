"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Diagnostics() {
  const [insights, setInsights] = useState<any[]>([]);
  const [briefData, setBriefData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [dismissingId, setDismissingId] = useState("");
  const [isTriggering, setIsTriggering] = useState(false);
  const [triggerMessage, setTriggerMessage] = useState("");

  const fetchData = async () => {
    try {
      const data = await api.getInsights();
      setInsights(data);
      
      try {
        const brief = await api.getBriefData();
        setBriefData(brief);
      } catch (briefErr) {
        console.error("Error al obtener datos del reporte:", briefErr);
      }
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

  const handleTriggerAnalysis = async () => {
    setIsTriggering(true);
    setTriggerMessage("");
    try {
      await api.triggerAnalysis();
      setTriggerMessage("Diagnóstico iniciado en segundo plano. Recarga en unos minutos.");
      // Opcional: Refrescar después de un rato o dejar que el usuario recargue
    } catch (error) {
      setTriggerMessage("Error al iniciar el diagnóstico.");
    } finally {
      setIsTriggering(false);
      setTimeout(() => setTriggerMessage(""), 5000);
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando diagnósticos...</div>;
  }

  return (
    <div style={{ height: "calc(100vh - 5rem)", display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Title & Action */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2 style={{ fontSize: "2rem", fontWeight: "700" }}>Análisis y Diagnósticos de la IA</h2>
          <p style={{ color: "var(--text-secondary)" }}>Master Brief Ejecutivo de optimización y clasificación de campañas.</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.5rem" }}>
          <button 
            onClick={handleTriggerAnalysis}
            disabled={isTriggering}
            className="btn-primary"
            style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}
          >
            {isTriggering ? (
              <>
                <div style={{ width: "16px", height: "16px", border: "2px solid #fff", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
                <span>Iniciando...</span>
              </>
            ) : (
              <>
                <span>✨</span>
                <span>Ejecutar Diagnóstico IA</span>
              </>
            )}
          </button>
          {triggerMessage && (
            <span style={{ fontSize: "0.85rem", color: "var(--accent-purple)", maxWidth: "250px", textAlign: "right" }}>
              {triggerMessage}
            </span>
          )}
        </div>
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

        {/* Master Brief Native UI */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", overflowY: "auto", height: "100%", paddingRight: "0.5rem" }}>
          {briefData ? (
            <>
              {/* KPI Grid */}
              <div style={{
                background: "var(--card-bg)",
                border: "1px solid var(--border-color)",
                borderRadius: "18px",
                padding: "1.5rem",
                boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.3)"
              }}>
                <h3 style={{ fontSize: "1.2rem", fontWeight: "600", marginBottom: "1rem", color: "#fff" }}>
                  📊 Resumen General (Últimos 7 Días)
                </h3>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "1rem" }}>
                  <div style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--border-color)", borderRadius: "12px", padding: "1rem", textAlign: "center" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", textTransform: "uppercase" }}>Presupuesto Diario Total</div>
                    <div style={{ fontSize: "1.5rem", fontWeight: "700", color: "#fff", marginTop: "0.25rem" }}>
                      S/.{Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.budget || 0), 0).toFixed(2)}
                    </div>
                  </div>
                  <div style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--border-color)", borderRadius: "12px", padding: "1rem", textAlign: "center" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", textTransform: "uppercase" }}>Inversión Total</div>
                    <div style={{ fontSize: "1.5rem", fontWeight: "700", color: "#fff", marginTop: "0.25rem" }}>
                      S/.{Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.spend || 0), 0).toFixed(2)}
                    </div>
                  </div>
                  <div style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--border-color)", borderRadius: "12px", padding: "1rem", textAlign: "center" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", textTransform: "uppercase" }}>Leads Totales</div>
                    <div style={{ fontSize: "1.5rem", fontWeight: "700", color: "#fff", marginTop: "0.25rem" }}>
                      {Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.conversions || 0), 0)}
                    </div>
                  </div>
                  <div style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--border-color)", borderRadius: "12px", padding: "1rem", textAlign: "center" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", textTransform: "uppercase" }}>CPL Promedio</div>
                    <div style={{ fontSize: "1.5rem", fontWeight: "700", color: "#fff", marginTop: "0.25rem" }}>
                      S/.{(() => {
                        const totalSpend = Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.spend || 0), 0);
                        const totalConvs = Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.conversions || 0), 0);
                        return totalConvs > 0 ? (totalSpend / totalConvs).toFixed(2) : totalSpend.toFixed(2);
                      })()}
                    </div>
                  </div>
                </div>
              </div>

              {/* Two columns: Narrative and Campaign Performance Table */}
              <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: "1.5rem" }}>
                {/* Column 1: Narrative */}
                <div style={{
                  background: "var(--card-bg)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "18px",
                  padding: "1.5rem",
                  boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.3)"
                }}>
                  <h3 style={{ fontSize: "1.2rem", fontWeight: "600", marginBottom: "1rem", color: "#fff", borderBottom: "1px solid rgba(255, 255, 255, 0.05)", paddingBottom: "0.5rem" }}>
                    🧠 Diagnóstico Narrativo de la IA
                  </h3>
                  <div style={{ fontSize: "0.95rem", color: "var(--text-primary)", lineHeight: "1.7", whiteSpace: "pre-line" }}>
                    {briefData.narrative}
                  </div>
                </div>

                {/* Column 2: Table */}
                <div style={{
                  background: "var(--card-bg)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "18px",
                  padding: "1.5rem",
                  boxShadow: "0 8px 32px 0 rgba(0, 0, 0, 0.3)",
                  overflowX: "auto"
                }}>
                  <h3 style={{ fontSize: "1.2rem", fontWeight: "600", marginBottom: "1rem", color: "#fff", borderBottom: "1px solid rgba(255, 255, 255, 0.05)", paddingBottom: "0.5rem" }}>
                    🎯 Desempeño de Campañas
                  </h3>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                    <thead>
                      <tr style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border-color)", textAlign: "left" }}>
                        <th style={{ padding: "0.5rem" }}>Campaña</th>
                        <th style={{ padding: "0.5rem" }}>Spend</th>
                        <th style={{ padding: "0.5rem" }}>Leads</th>
                        <th style={{ padding: "0.5rem" }}>CPL</th>
                        <th style={{ padding: "0.5rem" }}>CTR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.values(briefData.campaigns_data || {}).map((c: any) => {
                        const cpl = c.conversions > 0 ? c.spend / c.conversions : c.spend;
                        return (
                          <tr key={c.name} style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-primary)" }}>
                            <td style={{ padding: "0.75rem 0.5rem", fontWeight: "600" }}>{c.name}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>S/.{c.spend.toFixed(2)}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>{c.conversions}</td>
                            <td style={{ padding: "0.75rem 0.5rem", fontWeight: "700" }}>S/.{cpl.toFixed(2)}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>{c.ctr.toFixed(2)}%</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <div style={{ color: "var(--text-secondary)", textAlign: "center", padding: "4rem 2rem", background: "var(--card-bg)", borderRadius: "18px", border: "1px dashed var(--border-color)", display: "flex", flexDirection: "column", gap: "1rem", alignItems: "center" }}>
              <div style={{ fontSize: "3rem" }}>🤖</div>
              <h3 style={{ color: "#fff", fontSize: "1.2rem", fontWeight: "600" }}>Aún no hay diagnósticos generados</h3>
              <p style={{ maxWidth: "400px", lineHeight: "1.5" }}>El Master Brief Ejecutivo se genera analizando el rendimiento de tus campañas mediante Inteligencia Artificial.</p>
              <button onClick={handleTriggerAnalysis} disabled={isTriggering} className="btn-primary" style={{ marginTop: "1rem", padding: "0.5rem 1.5rem" }}>
                {isTriggering ? "Iniciando análisis..." : "Generar primer diagnóstico"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
