"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Diagnostics() {
  const [insights, setInsights] = useState<any[]>([]);
  const [briefData, setBriefData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dismissingId, setDismissingId] = useState("");
  const [isTriggering, setIsTriggering] = useState(false);
  const [triggerMessage, setTriggerMessage] = useState("");

  const fetchData = async () => {
    setError("");
    try {
      const data = await api.getInsights();
      setInsights(data);

      try {
        const brief = await api.getBriefData();
        setBriefData(brief);
      } catch (briefErr) {
        // El brief puede no existir todavía (400 sin datos); no es un error duro.
        console.error("Error al obtener datos del reporte:", briefErr);
      }
    } catch (e: any) {
      console.error("Error fetching insights:", e);
      setError(e?.message || "No se pudieron cargar los diagnósticos. Reintenta.");
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
          <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Análisis y Diagnósticos de la IA</h2>
          <p style={{ color: "var(--color-fog)", fontSize: "15px", marginTop: "4px" }}>Master Brief Ejecutivo de optimización y clasificación de campañas.</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.5rem" }}>
          <button
            onClick={handleTriggerAnalysis}
            disabled={isTriggering}
            className={briefData ? "btn-primary" : "btn-secondary"}
          >
            {isTriggering ? (
              <>
                <div style={{ width: "14px", height: "14px", border: "2px solid currentColor", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
                <span>Iniciando...</span>
              </>
            ) : (
              <span>Ejecutar Diagnóstico IA</span>
            )}
          </button>
          {triggerMessage && (
            <span role="status" aria-live="polite" style={{ fontSize: "13px", color: "var(--color-fog)", maxWidth: "250px", textAlign: "right" }}>
              {triggerMessage}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div role="alert" style={{
          background: "rgba(235, 87, 87, 0.1)",
          border: "1px solid rgba(235, 87, 87, 0.2)",
          color: "var(--color-coral-red)",
          borderRadius: "6px",
          padding: "0.75rem 1rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "1rem",
          fontSize: "14px"
        }}>
          <span>{error}</span>
          <button className="btn-secondary btn-sm" onClick={() => { setLoading(true); fetchData().finally(() => setLoading(false)); }}>
            Reintentar
          </button>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 2.8fr", gap: "2rem", flexGrow: 1, minHeight: 0 }}>
        {/* Sidebar Insights */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem", overflowY: "auto", paddingRight: "0.5rem" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 510, borderBottom: "1px solid var(--color-graphite)", paddingBottom: "0.5rem", color: "var(--color-paper)" }}>
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
                  background: "var(--color-carbon)",
                  boxShadow: ins.level === "CRITICAL" ? "rgba(235, 87, 87, 0.25) 0px 0px 0px 1px inset" : "var(--shadow-subtle)",
                  position: "relative"
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem", alignItems: "center" }}>
                  <span className={`badge ${ins.level === "CRITICAL" ? "badge-danger" : "badge-warning"}`}>
                    {ins.level}
                  </span>
                  <button
                    onClick={() => handleDismiss(ins.id)}
                    disabled={dismissingId === ins.id}
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--color-ash)",
                      cursor: "pointer",
                      fontSize: "12px"
                    }}
                  >
                    {dismissingId === ins.id ? "Descartando..." : "Descartar"}
                  </button>
                </div>
                <h4 style={{ fontSize: "14px", fontWeight: 510, marginBottom: "0.25rem", color: "var(--color-paper)" }}>{ins.title}</h4>
                <p style={{ fontSize: "13px", color: "var(--color-fog)", lineHeight: "1.5" }}>{ins.description}</p>
                {ins.suggested_action_type && (
                  <div className="mono" style={{ color: "var(--color-iris-violet)", marginTop: "0.5rem" }}>
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
              <div className="glass-card">
                <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1rem", color: "var(--color-paper)" }}>
                  Resumen General (Últimos 7 Días)
                </h3>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "1rem" }}>
                  <div className="card-subtle" style={{ textAlign: "center" }}>
                    <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>Presupuesto Diario Total</div>
                    <div style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", marginTop: "0.25rem", letterSpacing: "-0.012em" }}>
                      S/.{Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.budget || 0), 0).toFixed(2)}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ textAlign: "center" }}>
                    <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>Inversión Total</div>
                    <div style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", marginTop: "0.25rem", letterSpacing: "-0.012em" }}>
                      S/.{Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.spend || 0), 0).toFixed(2)}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ textAlign: "center" }}>
                    <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>Leads Totales</div>
                    <div style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", marginTop: "0.25rem", letterSpacing: "-0.012em" }}>
                      {Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.conversions || 0), 0)}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ textAlign: "center" }}>
                    <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>CPL Promedio</div>
                    <div style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", marginTop: "0.25rem", letterSpacing: "-0.012em" }}>
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
                <div className="glass-card">
                  <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1rem", color: "var(--color-paper)", borderBottom: "1px solid var(--color-graphite)", paddingBottom: "0.5rem" }}>
                    Diagnóstico Narrativo de la IA
                  </h3>
                  <div style={{ fontSize: "0.95rem", color: "var(--text-primary)", lineHeight: "1.7", whiteSpace: "pre-line" }}>
                    {briefData.narrative ? briefData.narrative : (
                      <span style={{ color: "var(--text-secondary)" }}>
                        La narrativa todavía no se generó. Presiona “Ejecutar Diagnóstico IA” para generarla en segundo plano y recarga en unos minutos.
                      </span>
                    )}
                  </div>
                </div>

                {/* Column 2: Table */}
                <div className="glass-card" style={{ overflowX: "auto" }}>
                  <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1rem", color: "var(--color-paper)", borderBottom: "1px solid var(--color-graphite)", paddingBottom: "0.5rem" }}>
                    Desempeño de Campañas
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
                            <td style={{ padding: "0.75rem 0.5rem", fontWeight: 510 }}>{c.name}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>S/.{c.spend.toFixed(2)}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>{c.conversions}</td>
                            <td style={{ padding: "0.75rem 0.5rem", fontWeight: 510, color: "var(--color-paper)" }}>S/.{cpl.toFixed(2)}</td>
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
            <div style={{ color: "var(--color-fog)", textAlign: "center", padding: "4rem 2rem", background: "var(--color-carbon)", borderRadius: "12px", border: "1px dashed var(--color-graphite)", display: "flex", flexDirection: "column", gap: "1rem", alignItems: "center" }}>
              <h3 style={{ color: "var(--color-paper)", fontSize: "18px", fontWeight: 590 }}>Aún no hay diagnósticos generados</h3>
              <p style={{ maxWidth: "400px", lineHeight: "1.5" }}>El Master Brief Ejecutivo se genera analizando el rendimiento de tus campañas mediante Inteligencia Artificial.</p>
              <button onClick={handleTriggerAnalysis} disabled={isTriggering} className="btn-primary" style={{ marginTop: "1rem" }}>
                {isTriggering ? "Iniciando análisis..." : "Generar primer diagnóstico"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
