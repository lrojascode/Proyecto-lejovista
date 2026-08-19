"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import { badgeIcon } from "@/lib/badge";
import type { AIInsight, BriefData, SyncLearningSnapshot } from "@/lib/types";

// Q2: color/etiqueta del veredicto de tendencia de cada sincronización.
const VERDICT_META: Record<string, { label: string; badge: string }> = {
  IMPROVING: { label: "Mejorando", badge: "badge-success" },
  DEGRADING: { label: "Empeorando", badge: "badge-danger" },
  STABLE: { label: "Estable", badge: "badge-neutral" },
  BASELINE: { label: "Referencia inicial", badge: "badge-neutral" },
  NO_DATA: { label: "Sin datos", badge: "badge-warning" },
};

export default function Diagnostics() {
  const { lastEvent } = useWebSocket();
  const [insights, setInsights] = useState<AIInsight[]>([]);
  const [briefData, setBriefData] = useState<BriefData | null>(null);
  const [learning, setLearning] = useState<SyncLearningSnapshot[]>([]);
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

      try {
        // Q2: trayectoria de aprendizaje por sincronización. Complementaria; si
        // aún no hay snapshots, no es un error.
        setLearning(await api.getSyncLearningHistory(10));
      } catch (learnErr) {
        console.error("Error al obtener la trayectoria de aprendizaje:", learnErr);
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

  // El análisis de IA corre automático encadenado tras cada sync — antes esta
  // página no tenía forma de enterarse y se quedaba con los insights viejos
  // hasta recargar a mano.
  useEffect(() => {
    if (!lastEvent || lastEvent.event_type !== "analysis_status") return;
    if (lastEvent.status === "COMPLETED") {
      setTriggerMessage("Diagnóstico actualizado.");
      fetchData();
      setTimeout(() => setTriggerMessage(""), 5000);
    }
  }, [lastEvent]);

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
      setTriggerMessage("Diagnóstico iniciado en segundo plano — esta página se actualiza sola al terminar.");
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
                    {badgeIcon(ins.level === "CRITICAL" ? "badge-danger" : "badge-warning")} {ins.level}
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
                    {/* E-2: métrica primaria unificada con /reporte — conversaciones
                        (con fallback a leads en el backend), mismo nombre en toda la app. */}
                    <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>Conversaciones</div>
                    <div style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", marginTop: "0.25rem", letterSpacing: "-0.012em" }}>
                      {Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.conversations ?? c.conversions ?? 0), 0)}
                    </div>
                  </div>
                  <div className="card-subtle" style={{ textAlign: "center" }}>
                    <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>Costo por conversación</div>
                    <div style={{ fontSize: "20px", fontWeight: 590, color: "var(--color-paper)", marginTop: "0.25rem", letterSpacing: "-0.012em" }}>
                      S/.{(() => {
                        const totalSpend = Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.spend || 0), 0);
                        const totalConvs = Object.values(briefData.campaigns_data || {}).reduce((acc: number, c: any) => acc + (c.conversations ?? c.conversions ?? 0), 0);
                        return totalConvs > 0 ? (totalSpend / totalConvs).toFixed(2) : totalSpend.toFixed(2);
                      })()}
                    </div>
                  </div>
                </div>
              </div>

              {/* Q2: Trayectoria de aprendizaje por sincronización */}
              <div className="glass-card">
                <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "0.25rem", color: "var(--color-paper)" }}>
                  Trayectoria de aprendizaje
                </h3>
                <p style={{ fontSize: "13px", color: "var(--color-fog)", marginBottom: "1rem" }}>
                  Lo que la IA aprende del rendimiento real de la cuenta en cada sincronización.
                </p>
                {learning.length === 0 ? (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>
                    Aún no hay aprendizaje registrado — se guarda automáticamente en la próxima sincronización.
                  </p>
                ) : (
                  <>
                    {/* Última sincronización, con sus observaciones */}
                    {(() => {
                      const latest = learning[0];
                      const meta = VERDICT_META[latest.verdict || "STABLE"] || VERDICT_META.STABLE;
                      return (
                        <div className="card-subtle" style={{ marginBottom: "1rem" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem", flexWrap: "wrap" }}>
                            <span className={`badge ${meta.badge}`}>{badgeIcon(meta.badge)} {meta.label}</span>
                            {latest.kpis && (
                              <span style={{ fontSize: "13px", color: "var(--color-fog)" }}>
                                S/.{latest.kpis.cost_per_conv.toFixed(2)}/conv · {latest.kpis.conversations} conv · S/.{latest.kpis.spend.toFixed(2)} gasto
                              </span>
                            )}
                            {latest.created_at && (
                              <span style={{ fontSize: "12px", color: "var(--color-ash)", marginLeft: "auto" }}>
                                {new Date(latest.created_at).toLocaleString("es-PE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                              </span>
                            )}
                          </div>
                          <ul style={{ margin: 0, paddingLeft: "1.1rem", display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                            {(latest.observations || []).map((o, i) => (
                              <li key={i} style={{ fontSize: "13px", color: "var(--text-primary)", lineHeight: "1.5" }}>{o}</li>
                            ))}
                          </ul>
                        </div>
                      );
                    })()}

                    {/* Sincronizaciones anteriores, compactas */}
                    {learning.length > 1 && (
                      <div style={{ display: "flex", gap: "0.5rem", overflowX: "auto", paddingBottom: "0.25rem" }}>
                        {learning.slice(1).map((s) => {
                          const meta = VERDICT_META[s.verdict || "STABLE"] || VERDICT_META.STABLE;
                          return (
                            <div key={s.id} style={{ flex: "0 0 auto", minWidth: "120px", padding: "0.5rem 0.75rem", background: "var(--color-carbon)", borderRadius: "8px", border: "1px solid var(--color-graphite)" }}>
                              <div style={{ fontSize: "11px", color: "var(--color-ash)", marginBottom: "0.25rem" }}>
                                {s.created_at ? new Date(s.created_at).toLocaleDateString("es-PE", { day: "2-digit", month: "short" }) : ""}
                              </div>
                              <span className={`badge ${meta.badge}`} style={{ fontSize: "0.65rem" }}>{meta.label}</span>
                              {s.kpis && (
                                <div style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "0.35rem" }}>
                                  S/.{s.kpis.cost_per_conv.toFixed(2)}/conv
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Two columns: Narrative and Campaign Performance Table */}
              <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: "1.5rem" }}>
                {/* Column 1: Narrative */}
                <div className="glass-card">
                  <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1rem", color: "var(--color-paper)", borderBottom: "1px solid var(--color-graphite)", paddingBottom: "0.5rem", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                    Diagnóstico Narrativo de la IA
                    {/* C-4: si el LLM no estuvo disponible, no se presenta la
                        plantilla local como análisis de la IA. */}
                    {briefData.narrative_provider === "fallback_template" && (
                      <span className="badge badge-warning" title="El servicio de IA no estuvo disponible; este es un informe básico generado con los datos reales de la cuenta.">
                        {badgeIcon("badge-warning")} Informe básico — IA no disponible
                      </span>
                    )}
                  </h3>
                  <div className="narrative-content" style={{ fontSize: "0.95rem", color: "var(--text-primary)", lineHeight: "1.7" }}>
                    {briefData.narrative ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{briefData.narrative}</ReactMarkdown>
                    ) : (
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
                        <th style={{ padding: "0.5rem" }}>Gasto</th>
                        <th style={{ padding: "0.5rem" }}>Conv.</th>
                        <th style={{ padding: "0.5rem" }}>Costo/Conv.</th>
                        <th style={{ padding: "0.5rem" }}>CTR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.values(briefData.campaigns_data || {}).map((c: any) => {
                        const convs = c.conversations ?? c.conversions ?? 0;
                        const cpl = convs > 0 ? c.spend / convs : c.spend;
                        return (
                          <tr key={c.name} style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-primary)" }}>
                            <td style={{ padding: "0.75rem 0.5rem", fontWeight: 510 }}>{c.name}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>S/.{c.spend.toFixed(2)}</td>
                            <td style={{ padding: "0.75rem 0.5rem" }}>{convs}</td>
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
