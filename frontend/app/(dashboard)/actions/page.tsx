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
  const [riskFilter, setRiskFilter] = useState<string>("ALL");
  const [typeFilter, setTypeFilter] = useState<string>("ALL");
  const [bulkLoading, setBulkLoading] = useState<boolean>(false);

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

  const filteredPending = pending.filter((act) => {
    if (riskFilter !== "ALL" && act.risk_level !== riskFilter) return false;
    if (typeFilter !== "ALL" && act.action_type !== typeFilter) return false;
    return true;
  });

  // Agrupación por campaña
  const groupedByCampaign = filteredPending.reduce((acc, act) => {
    const params = act.parameters as Record<string, any> | undefined;
    const campaignKey = params?.campaign_name || act.target_campaign_id || "Cuenta / General";
    if (!acc[campaignKey]) acc[campaignKey] = [];
    acc[campaignKey].push(act);
    return acc;
  }, {} as Record<string, AIAction[]>);

  // Ordenar dentro de cada grupo por riesgo (HIGH -> MEDIUM -> LOW)
  const riskWeight: Record<string, number> = { HIGH: 1, MEDIUM: 2, LOW: 3 };
  Object.values(groupedByCampaign).forEach((group) => {
    group.sort((a, b) => (riskWeight[a.risk_level] || 99) - (riskWeight[b.risk_level] || 99));
  });

  const lowRiskPending = filteredPending.filter((act) => act.risk_level === "LOW");
  const availableTypes = Array.from(new Set(pending.map((a) => a.action_type)));

  const latestProposedTime = pending.length > 0
    ? Math.max(...pending.map((a) => new Date(a.proposed_at).getTime()))
    : null;
  const latestAnalysisDate = latestProposedTime
    ? new Date(latestProposedTime).toLocaleString("es-ES", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  const handleApproveAllLowRisk = async () => {
    if (lowRiskPending.length === 0) return;
    setBulkLoading(true);
    setStatusMsg("");
    try {
      for (const act of lowRiskPending) {
        await api.approveAction(act.id, true);
      }
      setStatusMsg(`${lowRiskPending.length} acción(es) de riesgo bajo aprobadas.`);
      await fetchData();
    } catch (err: any) {
      setStatusMsg(`Error al aprobar lote: ${err.message}`);
    } finally {
      setBulkLoading(false);
    }
  };

  const handleRejectAllVisible = async () => {
    if (filteredPending.length === 0) return;
    if (!window.confirm(`¿Estás seguro de descartar las ${filteredPending.length} acción(es) mostradas?`)) return;
    setBulkLoading(true);
    setStatusMsg("");
    try {
      for (const act of filteredPending) {
        await api.approveAction(act.id, false, "Descartadas en lote");
      }
      setStatusMsg(`${filteredPending.length} acción(es) descartadas.`);
      await fetchData();
    } catch (err: any) {
      setStatusMsg(`Error al descartar lote: ${err.message}`);
    } finally {
      setBulkLoading(false);
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando cola de acciones...</div>;
  }

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "2rem" }}>
        <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>
          {latestAnalysisDate ? `Sugerencias del análisis del ${latestAnalysisDate}` : "Cola de Acciones Sugeridas"}
        </h2>
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
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 510, color: "var(--color-paper)" }}>
              Pendientes de Aprobación ({filteredPending.length})
            </h3>
            {/* Bulk actions */}
            {filteredPending.length > 0 && (
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                {lowRiskPending.length > 0 && (
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    onClick={handleApproveAllLowRisk}
                    disabled={bulkLoading || !!processingId}
                  >
                    {bulkLoading ? "Procesando..." : `Aprobar riesgo BAJO (${lowRiskPending.length})`}
                  </button>
                )}
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={handleRejectAllVisible}
                  disabled={bulkLoading || !!processingId}
                  style={{ color: "var(--color-coral-red)" }}
                >
                  Descartar todas
                </button>
              </div>
            )}
          </div>

          {/* Filters */}
          {pending.length > 0 && (
            <div style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", color: "var(--text-secondary)" }}>
                <span>Riesgo:</span>
                <select
                  className="form-input"
                  style={{ padding: "4px 8px", fontSize: "13px" }}
                  value={riskFilter}
                  onChange={(e) => setRiskFilter(e.target.value)}
                >
                  <option value="ALL">Todos los riesgos</option>
                  <option value="LOW">Bajo</option>
                  <option value="MEDIUM">Medio</option>
                  <option value="HIGH">Alto</option>
                </select>
              </div>

              {availableTypes.length > 1 && (
                <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", color: "var(--text-secondary)" }}>
                  <span>Tipo:</span>
                  <select
                    className="form-input"
                    style={{ padding: "4px 8px", fontSize: "13px" }}
                    value={typeFilter}
                    onChange={(e) => setTypeFilter(e.target.value)}
                  >
                    <option value="ALL">Todos los tipos</option>
                    {availableTypes.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}
          
          {pending.length === 0 ? (
            <div className="glass-card" style={{ textAlign: "center", padding: "3rem 1.5rem" }}>
              <p style={{ color: "var(--text-secondary)", fontSize: "0.95rem" }}>
                El último análisis no encontró nada que proponer.
              </p>
              <p style={{ color: "var(--color-fog)", fontSize: "0.85rem", marginTop: "0.5rem" }}>
                Tus campañas están funcionando dentro de los parámetros esperados.
              </p>
            </div>
          ) : filteredPending.length === 0 ? (
            <div className="glass-card" style={{ textAlign: "center", padding: "2rem 1.5rem" }}>
              <p style={{ color: "var(--text-secondary)" }}>No hay acciones que coincidan con los filtros seleccionados.</p>
            </div>
          ) : (
            Object.entries(groupedByCampaign).map(([campName, campActions]) => (
              <div key={campName} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div style={{ fontSize: "14px", fontWeight: 590, color: "var(--color-mist)", borderBottom: "1px solid var(--color-graphite)", paddingBottom: "0.25rem" }}>
                  📁 {campName} ({campActions.length})
                </div>
                {campActions.map((act) => {
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

                      {/* Creativo a reutilizar en la cuenta (2.3) */}
                      {(act.parameters as any)?.reused_creative && (
                        <div style={{
                          background: "rgba(255, 255, 255, 0.03)",
                          border: "1px solid var(--border-subtle)",
                          borderRadius: "6px",
                          padding: "8px 12px",
                          marginBottom: "1.25rem",
                          fontSize: "12px",
                        }}>
                          <div style={{ color: "var(--color-fog)", marginBottom: "4px" }}>Creativo (imagen) que se reutilizará:</div>
                          <div style={{ color: "var(--color-paper)", fontWeight: 500 }}>
                            🖼 {(act.parameters as any).reused_creative.name || "Creativo sincronizado"}
                            {(act.parameters as any).reused_creative.image_hash && (
                              <span className="mono" style={{ color: "var(--color-mist)", marginLeft: "8px", fontSize: "11px" }}>
                                (hash: {(act.parameters as any).reused_creative.image_hash})
                              </span>
                            )}
                          </div>
                        </div>
                      )}

                      <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end" }}>
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => setRejectId(act.id)}
                          disabled={processingId === act.id || bulkLoading}
                          style={{ borderColor: "rgba(235, 87, 87, 0.25)", color: "var(--color-coral-red)" }}
                        >
                          Rechazar
                        </button>
                        <button
                          className="btn-primary btn-sm"
                          onClick={() => handleApprove(act.id)}
                          disabled={processingId === act.id || bulkLoading}
                        >
                          {processingId === act.id ? "Encolando..." : "Aprobar y Ejecutar"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ))
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
                let statusLabel = act.status;
                if (act.status === "FAILED") statusBadge = "badge-danger";
                else if (act.status === "REJECTED") statusBadge = "badge-danger";
                else if (act.status === "EXECUTING") statusBadge = "badge-warning";
                else if (act.status === "SUPERSEDED") {
                  statusBadge = "badge-neutral";
                  statusLabel = "SUPERADA";
                }

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
                        {badgeIcon(statusBadge)} {statusLabel}
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

                    {act.status === "SUPERSEDED" && (
                      <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontStyle: "italic" }}>
                        Superada por un análisis posterior.
                      </p>
                    )}
                    
                    {act.status === "COMPLETED" && act.execution_result && (() => {
                      // execution_result es un JSON libre (varía según el tipo de
                      // acción) — el backend lo tipa como Dict[str, Any], sin forma
                      // fija que declarar aquí.
                      const result = act.execution_result as Record<string, any>;
                      return (
                        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                          {result.simulated ? (
                            <span style={{ color: "var(--color-amber)" }}>
                              Simulación — no se aplicó en Meta (write-back desactivado).
                            </span>
                          ) : (
                            <>Aplicado en Meta · ID: <code>{result.campaign_id || result.id || "OK"}</code></>
                          )}
                        </div>
                      );
                    })()}
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
