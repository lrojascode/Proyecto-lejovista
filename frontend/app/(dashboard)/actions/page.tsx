"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Actions() {
  const [pending, setPending] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [processingId, setProcessingId] = useState("");
  const [rejectId, setRejectId] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [statusMsg, setStatusMsg] = useState("");

  const fetchData = async () => {
    try {
      const pendingData = await api.getPendingActions();
      const historyData = await api.getActionHistory();
      setPending(pendingData);
      setHistory(historyData);
    } catch (e) {
      console.error("Error fetching actions:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  // Efecto para auto-refrescar si hay alguna acción ejecutándose
  useEffect(() => {
    const hasExecuting = history.some(act => act.status === "EXECUTING");
    if (hasExecuting) {
      const interval = setInterval(() => {
        fetchData();
      }, 3000);
      return () => clearInterval(interval);
    }
  }, [history]);

  const handleApprove = async (id: string) => {
    setProcessingId(id);
    setStatusMsg("");
    try {
      await api.approveAction(id, true);
      setStatusMsg("✓ Acción aprobada y encolada para ejecución.");
      await fetchData();
    } catch (err: any) {
      setStatusMsg(`❌ Error: ${err.message}`);
    } finally {
      setProcessingId("");
    }
  };

  const handleRejectSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rejectId) return;

    setProcessingId(rejectId);
    setStatusMsg("");
    try {
      await api.approveAction(rejectId, false, rejectReason);
      setStatusMsg("✓ Acción rechazada.");
      setRejectId("");
      setRejectReason("");
      await fetchData();
    } catch (err: any) {
      setStatusMsg(`❌ Error: ${err.message}`);
    } finally {
      setProcessingId("");
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando cola de acciones...</div>;
  }

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "2rem" }}>
        <h2 style={{ fontSize: "2rem", fontWeight: "700" }}>Cola de Acciones Sugeridas</h2>
        <p style={{ color: "var(--text-secondary)" }}>
          Aprueba o rechaza los cambios que el motor de IA ha planificado según sus análisis de seguridad.
        </p>
      </div>

      {statusMsg && (
        <div style={{
          background: "rgba(139, 92, 246, 0.1)",
          border: "1px solid rgba(139, 92, 246, 0.2)",
          color: "#fff",
          padding: "0.75rem 1rem",
          borderRadius: "8px",
          marginBottom: "1.5rem",
          fontSize: "0.9rem"
        }}>
          {statusMsg}
        </div>
      )}

      {/* Reject Reason modal simulation */}
      {rejectId && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(0,0,0,0.7)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 100
        }}>
          <form onSubmit={handleRejectSubmit} className="glass-card" style={{ maxWidth: "400px", width: "100%" }}>
            <h3 style={{ marginBottom: "1rem" }}>Rechazar Acción</h3>
            <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "1rem" }}>
              Por favor indica la razón para rechazar esta sugerencia (ayuda al aprendizaje de la IA):
            </p>
            <textarea
              className="form-input"
              required
              rows={3}
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Ej: CPL aún dentro del rango aceptable, prefiero esperar."
              style={{ width: "100%", marginBottom: "1.5rem", resize: "none" }}
            />
            <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
              <button type="button" className="btn-secondary" onClick={() => setRejectId("")}>Cancelar</button>
              <button type="submit" className="btn-primary" style={{ background: "var(--danger)" }}>Rechazar</button>
            </div>
          </form>
        </div>
      )}

      {/* Grid: Pending vs History */}
      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: "2rem" }}>
        
        {/* Pending Actions */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <h3 style={{ fontSize: "1.25rem", color: "#fff" }}>🤖 Pendientes de Aprobación ({pending.length})</h3>
          
          {pending.length === 0 ? (
            <div className="glass-card" style={{ textAlign: "center", padding: "3rem 1.5rem" }}>
              <p style={{ color: "var(--text-secondary)" }}>No hay acciones pendientes en este momento.</p>
            </div>
          ) : (
            pending.map((act) => {
              let riskBadge = "badge-success";
              if (act.risk_level === "HIGH") riskBadge = "badge-danger";
              else if (act.risk_level === "MEDIUM") riskBadge = "badge-warning";

              return (
                <div key={act.id} className="glass-card" style={{ padding: "1.75rem" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.75rem", alignItems: "center" }}>
                    <span className={`badge ${riskBadge}`}>Riesgo {act.risk_level}</span>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                      Propuesto: {new Date(act.proposed_at).toLocaleDateString()}
                    </span>
                  </div>

                  <h4 style={{ fontSize: "1.2rem", fontWeight: "600", marginBottom: "0.5rem", color: "#fff" }}>{act.title}</h4>
                  <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "1.25rem", lineHeight: "1.5" }}>
                    {act.description}
                  </p>

                  <div style={{
                    background: "rgba(255,255,255,0.02)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "10px",
                    padding: "1rem",
                    marginBottom: "1.5rem"
                  }}>
                    <div style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "0.25rem" }}>IMPACTO ESTIMADO</div>
                    <div style={{ fontSize: "0.95rem", color: "var(--success)", fontWeight: "600" }}>{act.estimated_impact}</div>
                  </div>

                  <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end" }}>
                    <button
                      className="btn-secondary"
                      onClick={() => setRejectId(act.id)}
                      disabled={processingId === act.id}
                      style={{ border: "1px solid rgba(239, 68, 68, 0.2)", color: "var(--danger)" }}
                    >
                      Rechazar
                    </button>
                    <button
                      className="btn-primary"
                      onClick={() => handleApprove(act.id)}
                      disabled={processingId === act.id}
                    >
                      {processingId === act.id ? "Encolando..." : "✅ Aprobar y Ejecutar"}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* History of Actions */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <h3 style={{ fontSize: "1.25rem", color: "#fff" }}>📜 Historial de Cambios</h3>
          
          <div className="glass-card" style={{ display: "flex", flexDirection: "column", gap: "1.25rem", maxHeight: "600px", overflowY: "auto" }}>
            {history.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>Aún no se han ejecutado ni rechazado acciones.</p>
            ) : (
              history.map((act) => {
                let statusBadge = "badge-success";
                if (act.status === "FAILED") statusBadge = "badge-danger";
                else if (act.status === "REJECTED") statusBadge = "badge-danger";
                else if (act.status === "EXECUTING") statusBadge = "badge-warning";

                return (
                  <div 
                    key={act.id} 
                    style={{ 
                      paddingBottom: "1rem", 
                      borderBottom: "1px solid var(--border-color)",
                      lastChild: { borderBottom: "none" }
                    } as any}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem", alignItems: "center" }}>
                      <strong style={{ fontSize: "0.9rem", color: "#fff" }}>{act.title}</strong>
                      <span className={`badge ${statusBadge}`} style={{ fontSize: "0.6rem" }}>
                        {act.status}
                      </span>
                    </div>
                    <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "0.25rem" }}>
                      {act.description}
                    </p>
                    
                    {act.status === "EXECUTING" && (
                      <div style={{ fontSize: "0.75rem", color: "var(--warning)", fontWeight: "600", animation: "pulse 1.5s infinite" }}>
                        ⌛ Ejecutando llamadas API con pausas anti-detección...
                      </div>
                    )}
                    
                    {act.status === "REJECTED" && (
                      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontStyle: "italic" }}>
                        Razón: {act.reject_reason}
                      </p>
                    )}
                    
                    {act.status === "COMPLETED" && act.execution_result && (
                      <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                        ID meta: <code>{act.execution_result.campaign_id || act.execution_result.id || "OK"}</code>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
