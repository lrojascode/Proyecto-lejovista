"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import Link from "next/link";

export default function CampaignsList() {
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
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
    fetchCampaigns();
  }, []);

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando lista de campañas...</div>;
  }

  return (
    <div>
      <div style={{ marginBottom: "2rem" }}>
        <h2 style={{ fontSize: "2rem", fontWeight: "700" }}>Todas las Campañas</h2>
        <p style={{ color: "var(--text-secondary)" }}>Consulta el rendimiento y estado de optimización de todas las campañas de anuncios.</p>
      </div>

      <div className="glass-card" style={{ padding: "2rem" }}>
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
                      <td style={{ padding: "1.25rem 0.5rem" }}>
                        <Link href={`/campaigns/${c.campaign_id}`} style={{ fontWeight: "700", color: "#fff", hover: { color: "var(--accent-purple)" } } as any}>
                          {c.campaign_name}
                        </Link>
                      </td>
                      <td style={{ padding: "1.25rem 0.5rem" }}>S/.{c.budget.toFixed(2)}</td>
                      <td style={{ padding: "1.25rem 0.5rem" }}>S/.{c.spend.toFixed(2)}</td>
                      <td style={{ padding: "1.25rem 0.5rem" }}>{c.conversions}</td>
                      <td style={{ padding: "1.25rem 0.5rem", fontWeight: "700" }}>S/.{cpl.toFixed(2)}</td>
                      <td style={{ padding: "1.25rem 0.5rem" }}>{c.frequency.toFixed(2)}</td>
                      <td style={{ padding: "1.25rem 0.5rem" }}>
                        <span className={`badge ${badgeClass}`}>{badgeLabel}</span>
                      </td>
                      <td style={{ padding: "1.25rem 0.5rem" }}>
                        <Link href={`/campaigns/${c.campaign_id}`} className="btn-secondary" style={{ padding: "0.4rem 0.8rem", fontSize: "0.85rem" }}>
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
