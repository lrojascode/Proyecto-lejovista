"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import type { HistoryStatus } from "@/lib/types";

/** Aviso de que todavía se está trayendo el historial de la cuenta.
 *
 * Se muestra SOLO la primera vez. Quién decide eso es el backend
 * (`show_estimate`): los sincronizados incrementales duran segundos y una barra
 * ahí sería ruido.
 *
 * El progreso es real, no una barra falsa: `SyncJob.payload_json` lleva
 * meses_hechos / meses_totales y el backfill lo actualiza al cerrar cada mes.
 * Se refresca por WebSocket y con un sondeo de respaldo, porque si el socket se
 * cae el aviso se quedaría clavado hasta recargar.
 */
const POLL_MS = 20000;

export function HistoryBackfillBanner() {
  const { lastEvent } = useWebSocket();
  const [status, setStatus] = useState<HistoryStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api.getHistoryStatus()
        .then((s) => { if (!cancelled) setStatus(s); })
        // Silencioso a propósito: si esto falla, lo correcto es no mostrar
        // nada. Un error acá no es algo que el cliente pueda accionar.
        .catch(() => {});
    };
    load();
    const t = setInterval(load, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  useEffect(() => {
    if (lastEvent?.event_type === "sync_status" && lastEvent.status === "COMPLETED") {
      api.getHistoryStatus().then(setStatus).catch(() => {});
    }
  }, [lastEvent]);

  if (!status?.show_estimate) return null;

  const done = status.in_progress?.months_done ?? 0;
  const total = status.in_progress?.months_total ?? status.months_missing;
  const corriendo = status.in_progress != null && total > 0;
  const pct = corriendo && total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  return (
    <div
      className="glass-card"
      role="status"
      style={{ marginBottom: "16px", padding: "12px 16px", display: "flex", flexDirection: "column", gap: "8px" }}
    >
      <div style={{ fontSize: "14px", color: "var(--color-mist)" }}>
        {corriendo
          ? `Trayendo el historial de tu cuenta: ${done} de ${total} ${total === 1 ? "mes" : "meses"}.`
          : status.message}
      </div>
      {corriendo ? (
        <div style={{ height: "4px", background: "var(--color-smoke)", borderRadius: "2px", overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--color-accent)", transition: "width 400ms ease" }} />
        </div>
      ) : null}
      {corriendo && done >= total ? (
        <div className="mono" style={{ fontSize: "12px", color: "var(--color-fog)" }}>
          Está tardando más de lo estimado; sigue trabajando.
        </div>
      ) : null}
    </div>
  );
}
