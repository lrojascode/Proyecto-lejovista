"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import { badgeIcon } from "@/lib/badge";
import type { AIAction } from "@/lib/types";

// 5.4(a) (E-6): focus trap + Escape + aria-modal para el modal de rechazo —
// antes era un overlay visual sin ninguna de las tres cosas: el foco del
// teclado seguía "detrás" del modal y Escape no lo cerraba.
function RejectModal({
  reason, onReasonChange, onCancel, onSubmit, submitting,
}: {
  reason: string; onReasonChange: (v: string) => void;
  onCancel: () => void; onSubmit: (e: React.FormEvent) => void; submitting: boolean;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const triggerRef = useRef<Element | null>(null);

  useEffect(() => {
    triggerRef.current = document.activeElement;
    textareaRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, textarea, [href], input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      (triggerRef.current as HTMLElement)?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{
      position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
      background: "rgba(8, 9, 10, 0.8)", display: "flex",
      alignItems: "center", justifyContent: "center", zIndex: 100,
    }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="reject-modal-title"
      >
        <form onSubmit={onSubmit} className="glass-card" style={{ maxWidth: "400px", width: "100%" }}>
          <h3 id="reject-modal-title" style={{ marginBottom: "1rem" }}>Rechazar Acción</h3>
          <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "1rem" }}>
            Por favor indica la razón para rechazar esta sugerencia (ayuda al aprendizaje de la IA):
          </p>
          <textarea
            ref={textareaRef}
            className="form-input"
            required
            rows={3}
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
            placeholder="Ej: CPL aún dentro del rango aceptable, prefiero esperar."
            style={{ width: "100%", marginBottom: "1.5rem", resize: "none" }}
          />
          <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
            <button type="button" className="btn-secondary" onClick={onCancel}>Cancelar</button>
            <button type="submit" className="btn-danger" disabled={submitting}>
              {submitting ? "Rechazando..." : "Rechazar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function Actions() {
  const { lastEvent } = useWebSocket();
  const [pending, setPending] = useState<AIAction[]>([]);
  const [history, setHistory] = useState<AIAction[]>([]);
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

  // El planner de IA propone acciones nuevas dentro del mismo análisis que
  // corre automático tras cada sync — sin esto, una acción recién propuesta
  // no aparecía acá hasta recargar la página a mano.
  useEffect(() => {
    if (!lastEvent || lastEvent.event_type !== "analysis_status") return;
    if (lastEvent.status === "COMPLETED") {
      setStatusMsg("Nuevas acciones sugeridas disponibles.");
      fetchData();
      setTimeout(() => setStatusMsg(""), 5000);
    }
  }, [lastEvent]);

  const handleApprove = async (id: string) => {
    setProcessingId(id);
    setStatusMsg("");
    try {
      await api.approveAction(id, true);
      setStatusMsg("Acción aprobada y encolada para ejecución.");
      await fetchData();
    } catch (err: any) {
      setStatusMsg(`Error: ${err.message}`);
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
      setStatusMsg("Acción rechazada.");
      setRejectId("");
      setRejectReason("");
      await fetchData();
    } catch (err: any) {
      setStatusMsg(`Error: ${err.message}`);
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
        <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Cola de Acciones Sugeridas</h2>
        <p style={{ color: "var(--text-secondary)" }}>
          Aprueba o rechaza los cambios que el motor de IA ha planificado según sus análisis de seguridad.
        </p>
        <div role="note" className="card-subtle" style={{
          marginTop: "0.75rem",
          fontSize: "13px",
          color: "var(--color-fog)",
          padding: "8px 12px"
        }}>
          Al aprobar, la acción se ejecuta solo si el <strong>write-back a Meta</strong> está habilitado.
          Si está desactivado, la ejecución es una simulación y se indica como tal en el historial.
        </div>
      </div>

      {statusMsg && (
        <div role="status" aria-live="polite" className="card-subtle" style={{
          color: "var(--color-mist)",
          padding: "0.75rem 1rem",
          marginBottom: "1.5rem",
          fontSize: "14px"
        }}>
          {statusMsg}
        </div>
      )}

      {/* Modal de rechazo (5.4a): focus trap + Escape + aria-modal */}
      {rejectId && (
        <RejectModal
          reason={rejectReason}
          onReasonChange={setRejectReason}
          onCancel={() => { setRejectId(""); setRejectReason(""); }}
          onSubmit={handleRejectSubmit}
          submitting={processingId === rejectId}
        />
      )}

      {/* Grid: Pending vs History */}
      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: "2rem" }}>
        
        {/* Pending Actions */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 510, color: "var(--color-paper)" }}>Pendientes de Aprobación ({pending.length})</h3>
          
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
                <div key={act.id} className="glass-card">
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.75rem", alignItems: "center" }}>
                    <span className={`badge ${riskBadge}`}>{badgeIcon(riskBadge)} Riesgo {act.risk_level}</span>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                      Propuesto: {new Date(act.proposed_at).toLocaleDateString()}
                    </span>
                  </div>

                  <h4 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "0.5rem", color: "var(--color-paper)" }}>{act.title}</h4>
                  <p style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "1.25rem", lineHeight: "1.5" }}>
                    {act.description}
                  </p>

                  <div className="card-subtle" style={{ marginBottom: "1.5rem" }}>
                    <div style={{ fontSize: "12px", color: "var(--color-ash)", marginBottom: "0.25rem" }}>Impacto estimado</div>
                    <div style={{ fontSize: "14px", color: "var(--color-pulse-green)", fontWeight: 510 }}>{act.estimated_impact}</div>
                  </div>

                  <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end" }}>
                    <button
                      className="btn-secondary btn-sm"
                      onClick={() => setRejectId(act.id)}
                      disabled={processingId === act.id}
                      style={{ borderColor: "rgba(235, 87, 87, 0.25)", color: "var(--color-coral-red)" }}
                    >
                      Rechazar
                    </button>
                    <button
                      className="btn-primary btn-sm"
                      onClick={() => handleApprove(act.id)}
                      disabled={processingId === act.id}
                    >
                      {processingId === act.id ? "Encolando..." : "Aprobar y Ejecutar"}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* History of Actions */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 510, color: "var(--color-paper)" }}>Historial de Cambios</h3>
          
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
                      borderBottom: "1px solid var(--border-color)"
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem", alignItems: "center" }}>
                      <strong style={{ fontSize: "14px", fontWeight: 510, color: "var(--color-paper)" }}>{act.title}</strong>
                      <span className={`badge ${statusBadge}`}>
                        {badgeIcon(statusBadge)} {act.status}
                      </span>
                    </div>
                    <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "0.25rem" }}>
                      {act.description}
                    </p>
                    
                    {act.status === "EXECUTING" && (
                      <div style={{ fontSize: "12px", color: "var(--color-amber)", fontWeight: 510, animation: "pulse 1.5s infinite" }}>
                        Ejecutando llamadas API con pausas anti-detección...
                      </div>
                    )}
                    
                    {act.status === "REJECTED" && (
                      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontStyle: "italic" }}>
                        Razón: {act.reject_reason}
                      </p>
                    )}
                    
                    {act.status === "COMPLETED" && act.execution_result && (
                      <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                        {act.execution_result.simulated ? (
                          <span style={{ color: "var(--color-amber)" }}>
                            Simulación — no se aplicó en Meta (write-back desactivado).
                          </span>
                        ) : (
                          <>Aplicado en Meta · ID: <code>{act.execution_result.campaign_id || act.execution_result.id || "OK"}</code></>
                        )}
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
