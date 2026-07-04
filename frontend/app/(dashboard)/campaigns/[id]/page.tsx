"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import Link from "next/link";

export default function CampaignDetail() {
  const params = useParams();
  const router = useRouter();
  const campaignId = params.id as string;

  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!campaignId) return;

    const fetchHistory = async () => {
      try {
        const data = await api.getCampaignHistory(campaignId);
        setHistory(data);
      } catch (e) {
        console.error("Error fetching campaign history:", e);
      } finally {
        setLoading(false);
      }
    };
    fetchHistory();
  }, [campaignId]);

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando historial de campaña...</div>;
  }

  if (history.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: "3rem" }}>
        <p style={{ color: "var(--text-secondary)", marginBottom: "1.5rem" }}>No se encontró historial para esta campaña.</p>
        <Link href="/campaigns" className="btn-secondary">Volver a Campañas</Link>
      </div>
    );
  }

  // Tomamos el snapshot más reciente para los breakdowns
  const latest = history[history.length - 1];
  
  // Calcular promedios
  const totalSpend = history.reduce((acc, curr) => acc + curr.spend, 0);
  const totalLeads = history.reduce((acc, curr) => acc + curr.conversions, 0);
  const averageCpl = totalLeads > 0 ? totalSpend / totalLeads : totalSpend;

  return (
    <div>
      {/* Breadcrumbs */}
      <div style={{ marginBottom: "16px", display: "flex", gap: "8px", fontSize: "13px", color: "var(--color-fog)" }}>
        <Link href="/campaigns" className="campaign-link" style={{ fontWeight: 400, fontSize: "13px", color: "var(--color-fog)" }}>Campañas</Link>
        <span>/</span>
        <span style={{ color: "var(--color-paper)" }}>Detalle</span>
      </div>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "24px", gap: "16px", flexWrap: "wrap" }}>
        <div>
          <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>{latest.campaign_name}</h2>
          <p style={{ color: "var(--color-fog)", fontSize: "14px", marginTop: "4px" }}>ID de Meta Ads: <code>{campaignId}</code> • Estado: <strong style={{ color: "var(--color-paper)", fontWeight: 510 }}>{latest.status}</strong></p>
        </div>
        <Link href="/campaigns" className="btn-secondary btn-sm">Volver</Link>
      </div>

      {/* KPI Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "16px", marginBottom: "32px" }}>
        <div className="glass-card">
          <div style={{ color: "var(--color-fog)", fontSize: "12px" }}>Inversión Acumulada</div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "8px", color: "var(--color-paper)", letterSpacing: "-0.012em" }}>S/.{totalSpend.toFixed(2)}</div>
        </div>
        <div className="glass-card">
          <div style={{ color: "var(--color-fog)", fontSize: "12px" }}>Leads Totales</div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "8px", color: "var(--color-paper)", letterSpacing: "-0.012em" }}>{totalLeads}</div>
        </div>
        <div className="glass-card">
          <div style={{ color: "var(--color-fog)", fontSize: "12px" }}>CPL Histórico Promedio</div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "8px", color: "var(--color-paper)", letterSpacing: "-0.012em" }}>S/.{averageCpl.toFixed(2)}</div>
        </div>
        <div className="glass-card">
          <div style={{ color: "var(--color-fog)", fontSize: "12px" }}>Frecuencia Actual</div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "8px", color: "var(--color-paper)", letterSpacing: "-0.012em" }}>{latest.frequency.toFixed(2)}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2.3fr 1.7fr", gap: "2rem" }}>
        
        {/* Daily History Table */}
        <div className="glass-card" style={{ padding: "1.75rem" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "16px" }}>Historial Diario (Últimos 30 Días)</h3>
          <div style={{ overflowX: "auto", maxHeight: "500px" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-color)", textAlign: "left" }}>
                  <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Fecha</th>
                  <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Inversión</th>
                  <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Leads</th>
                  <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>CPL</th>
                  <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>CTR</th>
                  <th style={{ padding: "0.75rem 0.5rem", color: "var(--text-secondary)", fontSize: "0.85rem" }}>Frecuencia</th>
                </tr>
              </thead>
              <tbody>
                {[...history].reverse().map((s) => {
                  const dayCpl = s.conversions > 0 ? s.spend / s.conversions : s.spend;
                  return (
                    <tr key={s.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td style={{ padding: "0.85rem 0.5rem", fontWeight: 510 }}>{new Date(s.date).toLocaleDateString()}</td>
                      <td style={{ padding: "0.85rem 0.5rem" }}>S/.{s.spend.toFixed(2)}</td>
                      <td style={{ padding: "0.85rem 0.5rem" }}>{s.conversions}</td>
                      <td style={{ padding: "0.85rem 0.5rem", fontWeight: 510, color: "var(--color-paper)" }}>S/.{dayCpl.toFixed(2)}</td>
                      <td style={{ padding: "0.85rem 0.5rem" }}>{s.ctr.toFixed(2)}%</td>
                      <td style={{ padding: "0.85rem 0.5rem" }}>{s.frequency.toFixed(2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Breakdowns */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          
          {/* Placements Card */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "16px" }}>Desempeño por Placement</h3>
            {latest.placements_breakdown && Object.keys(latest.placements_breakdown).length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {Object.entries(latest.placements_breakdown).map(([plName, plData]: [string, any]) => (
                  <div key={plName} style={{ borderBottom: "1px solid var(--border-color)", paddingBottom: "0.75rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 510, marginBottom: "0.25rem" }}>
                      <span style={{ fontSize: "0.9rem", color: "#fff" }}>{plName.replace(/_/g, " ").toUpperCase()}</span>
                      <span style={{ fontSize: "0.9rem", color: plData.cpl < 3.5 ? "var(--success)" : "inherit" }}>
                        CPL: S/.{plData.cpl?.toFixed(2) || "0.00"}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                      <span>Spend: S/.{plData.spend?.toFixed(2) || "0.00"}</span>
                      <span>Leads: {plData.conversions || 0}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No hay información de desglose por placement.</p>
            )}
          </div>

          {/* Demographics Card */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "16px" }}>Edad y Segmentación</h3>
            {latest.demographics_breakdown && Object.keys(latest.demographics_breakdown).length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {Object.entries(latest.demographics_breakdown).map(([ageRange, ageData]: [string, any]) => (
                  <div key={ageRange} style={{ borderBottom: "1px solid var(--border-color)", paddingBottom: "0.75rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 510, marginBottom: "0.25rem" }}>
                      <span style={{ fontSize: "0.9rem", color: "#fff" }}>Rango: {ageRange}</span>
                      <span style={{ fontSize: "0.9rem" }}>Leads: {ageData.conversions || 0}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                      <span>Spend: S/.{ageData.spend?.toFixed(2) || "0.00"}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No hay información de desglose demográfico.</p>
            )}
          </div>
          
        </div>
      </div>
    </div>
  );
}
