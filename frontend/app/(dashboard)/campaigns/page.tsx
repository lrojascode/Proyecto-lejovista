"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import Link from "next/link";
import { badgeIcon } from "@/lib/badge";
import { StatusBadge } from "@/components/StatusBadge";
import type { CampaignSnapshot } from "@/lib/types";

export default function CampaignsList() {
  const { lastEvent } = useWebSocket();
  const [campaigns, setCampaigns] = useState<CampaignSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncMessage, setSyncMessage] = useState("");

  const fetchCampaigns = async () => {
    try {
      const data = await api.getRecentCampaigns();
      setCampaigns(data);
    } catch (e) {
      console.error("Error fetching campaigns list:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCampaigns();
  }, []);

  // Antes esta página solo cargaba una vez al entrar — si sincronizabas
  // estando acá, se quedaba con los datos viejos hasta recargar a mano.
  useEffect(() => {
    if (!lastEvent || lastEvent.event_type !== "sync_status") return;
    if (lastEvent.status === "COMPLETED") {
      setSyncMessage("Sincronización completada — datos actualizados.");
      fetchCampaigns();
    } else if (lastEvent.status === "FAILED") {
      setSyncMessage("La sincronización falló.");
    } else {
      setSyncMessage("Sincronizando datos de Meta Ads...");
    }
  }, [lastEvent]);

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando lista de campañas...</div>;
  }

  return (
    <div>
      <div style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
        <div>
          <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Todas las Campañas</h2>
          <p style={{ color: "var(--color-fog)", fontSize: "15px", marginTop: "4px" }}>Consulta el rendimiento y estado de optimización de todas las campañas de anuncios.</p>
          {syncMessage && (
            <p className="mono" style={{ color: "var(--color-signal-teal)", fontSize: "13px", marginTop: "8px" }}>{syncMessage}</p>
          )}
        </div>
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          <Link href="/campaigns/learning" className="btn-secondary" style={{ whiteSpace: "nowrap" }}>
            Aprendizaje de la IA
          </Link>
          <Link href="/campaigns/new" className="btn-primary" style={{ whiteSpace: "nowrap" }}>
            + Crear Campaña
          </Link>
        </div>
      </div>

      <div className="glass-card">
        {campaigns.length === 0 ? (
          <p style={{ color: "var(--text-secondary)", textAlign: "center" }}>
            No hay campañas sincronizadas. Conecta una cuenta de Meta Ads en Configuración.
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-color)", textAlign: "left" }}>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Campaña</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Presupuesto Diario</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Inversión (7D)</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Leads</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>CPL Promedio</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Frecuencia</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Estado</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Rendimiento</th>
                  <th style={{ padding: "1rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Acciones</th>
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
                      <td style={{ padding: "12px 8px" }}>
                        <Link href={`/campaigns/${c.campaign_id}`} className="campaign-link">
                          {c.campaign_name}
                        </Link>
                      </td>
                      <td style={{ padding: "12px 8px", fontSize: "14px" }}>S/.{c.budget.toFixed(2)}</td>
                      <td style={{ padding: "12px 8px", fontSize: "14px" }}>S/.{c.spend.toFixed(2)}</td>
                      <td style={{ padding: "12px 8px", fontSize: "14px" }}>{c.conversions}</td>
                      <td style={{ padding: "12px 8px", fontSize: "14px", fontWeight: 510, color: "var(--color-paper)" }}>S/.{cpl.toFixed(2)}</td>
                      <td style={{ padding: "12px 8px", fontSize: "14px" }}>{c.frequency.toFixed(2)}</td>
                      <td style={{ padding: "12px 8px" }}>
                        <StatusBadge status={c.status} />
                      </td>
                      <td style={{ padding: "12px 8px" }}>
                        <span className={`badge ${badgeClass}`}>{badgeIcon(badgeClass)} {badgeLabel}</span>
                      </td>
                      <td style={{ padding: "12px 8px" }}>
                        <Link href={`/campaigns/${c.campaign_id}`} className="btn-secondary btn-sm">
                          Ver Historial
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
    </div>
  );
}
