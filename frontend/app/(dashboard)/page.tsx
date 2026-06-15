"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import Link from "next/link";

export default function Dashboard() {
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [insights, setInsights] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [msg, setMsg] = useState("");

  const fetchData = async () => {
    try {
      const campsData = await api.getRecentCampaigns();
      const insightsData = await api.getInsights();
      setCampaigns(campsData);
      setInsights(insightsData);
    } catch (e) {
      console.error("Error fetching dashboard data:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleSync = async () => {
    setSyncing(true);
    setMsg("");
    try {
      const res = await api.triggerSync();
      setMsg("✓ Sincronización programada en segundo plano.");
      // Recargar datos tras un momento para ver los cambios mock
      setTimeout(fetchData, 1500);
    } catch (err: any) {
      setMsg(`❌ Error: ${err.message}`);
    } finally {
      setSyncing(false);
    }
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    setMsg("");
    try {
      await api.triggerAnalysis();
      setMsg("✓ Análisis de IA programado en segundo plano.");
      setTimeout(fetchData, 1500);
    } catch (err: any) {
      setMsg(`❌ Error: ${err.message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando métricas...</div>;
  }

  // Cálculos agregados
  const totalSpend = campaigns.reduce((acc, curr) => acc + curr.spend, 0);
  const totalLeads = campaigns.reduce((acc, curr) => acc + curr.conversions, 0);
  const averageCpl = totalLeads > 0 ? totalSpend / totalLeads : totalSpend;
  const activeCount = campaigns.filter(c => c.status === "ACTIVE").length;

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2rem" }}>
        <div>
          <h2 style={{ fontSize: "2rem", fontWeight: "700" }}>Resumen Ejecutivo</h2>
          <p style={{ color: "var(--text-secondary)" }}>Visión general de tus campañas y alertas de optimización autónoma.</p>
        </div>
        
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "center" }}>
          {msg && <span style={{ fontSize: "0.85rem", color: "var(--accent-purple)" }}>{msg}</span>}
          <button className="btn-secondary" onClick={handleSync} disabled={syncing}>
            {syncing ? "Sincronizando..." : "🔄 Sincronizar API"}
          </button>
          <button className="btn-primary" onClick={handleAnalyze} disabled={analyzing}>
            {analyzing ? "Analizando..." : "🧠 Correr IA"}
          </button>
        </div>
      </div>

      {/* KPI Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "1.5rem", marginBottom: "2.5rem" }}>
        <div className="glass-card">
          <div style={{ color: "var(--text-secondary)", fontSize: "0.85rem", textTransform: "uppercase" }}>Inversión (7D)</div>
          <div style={{ fontSize: "2rem", fontWeight: "700", marginTop: "0.5rem" }}>S/.{totalSpend.toFixed(2)}</div>
        </div>
        <div className="glass-card">
          <div style={{ color: "var(--text-secondary)", fontSize: "0.85rem", textTransform: "uppercase" }}>Leads Captados (7D)</div>
          <div style={{ fontSize: "2rem", fontWeight: "700", marginTop: "0.5rem" }}>{totalLeads}</div>
        </div>
        <div className="glass-card">
          <div style={{ color: "var(--text-secondary)", fontSize: "0.85rem", textTransform: "uppercase" }}>CPL Promedio</div>
          <div style={{ fontSize: "2rem", fontWeight: "700", marginTop: "0.5rem", color: averageCpl < 3.5 ? "var(--success)" : "inherit" }}>
            S/.{averageCpl.toFixed(2)}
          </div>
        </div>
        <div className="glass-card">
          <div style={{ color: "var(--text-secondary)", fontSize: "0.85rem", textTransform: "uppercase" }}>Campañas Activas</div>
          <div style={{ fontSize: "2rem", fontWeight: "700", marginTop: "0.5rem" }}>{activeCount} / {campaigns.length}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2.5fr 1.5fr", gap: "2rem" }}>
        {/* Table of campaigns */}
        <div className="glass-card" style={{ padding: "1.75rem" }}>
          <h3 style={{ fontSize: "1.25rem", marginBottom: "1.25rem" }}>🎯 Rendimiento por Campaña</h3>
          {campaigns.length === 0 ? (
            <p style={{ color: "var(--text-secondary)" }}>No hay datos. Presiona "Sincronizar API" para descargar datos de Meta Ads.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-color)", textAlign: "left" }}>
                    <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Nombre</th>
                    <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Spend</th>
                    <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Leads</th>
                    <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>CPL</th>
                    <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Estado</th>
                    <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}></th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.map((c) => {
                    const cpl = c.conversions > 0 ? c.spend / c.conversions : c.spend;
                    let badgeClass = "badge-success";
                    let badgeLabel = "Ganadora";
                    
                    if (cpl > 7.0 || c.conversions === 0) {
                      badgeClass = "badge-danger";
                      badgeLabel = "Fallida";
                    } else if (cpl > 3.5 || c.frequency > 3.0) {
                      badgeClass = "badge-warning";
                      badgeLabel = "Alerta";
                    }

                    return (
                      <tr key={c.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                        <td style={{ padding: "1rem 0.5rem", fontWeight: "600" }}>{c.campaign_name}</td>
                        <td style={{ padding: "1rem 0.5rem" }}>S/.{c.spend.toFixed(2)}</td>
                        <td style={{ padding: "1rem 0.5rem" }}>{c.conversions}</td>
                        <td style={{ padding: "1rem 0.5rem", fontWeight: "700" }}>S/.{cpl.toFixed(2)}</td>
                        <td style={{ padding: "1rem 0.5rem" }}>
                          <span className={`badge ${badgeClass}`}>{badgeLabel}</span>
                        </td>
                        <td style={{ padding: "1rem 0.5rem" }}>
                          <Link href={`/campaigns/${c.campaign_id}`} className="btn-secondary" style={{ padding: "0.4rem 0.8rem", fontSize: "0.8rem" }}>
                            Detalle
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Sidebar Alerts */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <div className="glass-card">
            <h3 style={{ fontSize: "1.25rem", marginBottom: "1.25rem" }}>💡 Hallazgos de IA</h3>
            {insights.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No se encontraron alertas críticas en la cuenta.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {insights.slice(0, 3).map((ins) => (
                  <div 
                    key={ins.id} 
                    style={{ 
                      padding: "1rem", 
                      borderRadius: "10px", 
                      background: ins.level === "CRITICAL" ? "rgba(239, 68, 68, 0.05)" : "rgba(245, 158, 11, 0.05)",
                      border: `1px solid ${ins.level === "CRITICAL" ? "rgba(239, 68, 68, 0.15)" : "rgba(245, 158, 11, 0.15)"}` 
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                      <strong style={{ fontSize: "0.9rem", color: ins.level === "CRITICAL" ? "var(--danger)" : "var(--warning)" }}>
                        {ins.title}
                      </strong>
                      <span className={`badge ${ins.level === "CRITICAL" ? "badge-danger" : "badge-warning"}`} style={{ fontSize: "0.6rem" }}>
                        {ins.level}
                      </span>
                    </div>
                    <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>{ins.description}</p>
                  </div>
                ))}
                
                <Link href="/diagnostics" style={{ color: "var(--accent-purple)", fontSize: "0.9rem", alignSelf: "flex-end", fontWeight: "600" }}>
                  Ver todos los diagnósticos →
                </Link>
              </div>
            )}
          </div>

          <div className="glass-card" style={{ background: "linear-gradient(135deg, rgba(139, 92, 246, 0.1) 0%, rgba(59, 130, 246, 0.1) 100%)" }}>
            <h3 style={{ fontSize: "1.25rem", marginBottom: "0.5rem" }}>🤖 Modo Autónomo</h3>
            <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "1rem" }}>
              La IA analiza y propone mejoras a la cola de aprobación. Puedes configurar las políticas y el presupuesto máximo en Configuración.
            </p>
            <Link href="/actions" className="btn-primary" style={{ width: "100%" }}>
              Cola de Acciones
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
