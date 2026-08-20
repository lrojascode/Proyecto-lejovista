"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import Link from "next/link";
import { badgeIcon } from "@/lib/badge";
import type { CampaignDetail } from "@/lib/types";
import { money } from "@/lib/format";

function formatSnapshotDate(dateOnly: string): string {
  if (!dateOnly) return "";
  return new Date(`${dateOnly}T00:00:00`).toLocaleDateString();
}

function fmt(n: number) {
  return (n || 0).toLocaleString("es-PE");
}

export default function CampaignDetailPage() {
  const params = useParams();
  const campaignId = params.id as string;

  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [activeTab, setActiveTab] = useState<"hierarchy" | "history" | "actions" | "breakdowns">("hierarchy");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!campaignId) return;
    setLoading(true);
    setError("");

    api.getCampaignDetails(campaignId)
      .then((d: CampaignDetail) => setDetail(d))
      .catch((err: any) => setError(err.message || "No se pudo cargar el detalle de la campaña."))
      .finally(() => setLoading(false));
  }, [campaignId]);

  if (loading) {
    return (
      <div className="glass-card" style={{ maxWidth: "1080px", margin: "32px auto 0 auto", textAlign: "center", padding: "3rem" }}>
        <p style={{ color: "var(--color-fog)" }}>Cargando estructura y métricas de la campaña...</p>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="glass-card" style={{ maxWidth: "800px", margin: "32px auto 0 auto", textAlign: "center", padding: "3rem" }}>
        <p style={{ color: "var(--color-coral-red)", marginBottom: "1.5rem" }}>
          {error || "No se encontró información para esta campaña."}
        </p>
        <Link href="/campaigns" className="btn-secondary">
          Volver a Campañas
        </Link>
      </div>
    );
  }

  const latest = detail.history.length > 0 ? detail.history[detail.history.length - 1] : null;
  const isPaused = detail.status === "PAUSED";
  const badgeClass = isPaused ? "badge-warning" : detail.status === "ACTIVE" ? "badge-success" : "badge-neutral";

  return (
    <div style={{ maxWidth: "1080px", margin: "24px auto 48px auto" }}>
      {/* Breadcrumbs */}
      <div style={{ marginBottom: "16px", display: "flex", gap: "8px", fontSize: "13px", color: "var(--color-fog)" }}>
        <Link href="/campaigns" className="campaign-link" style={{ fontWeight: 400, fontSize: "13px", color: "var(--color-fog)" }}>
          Campañas
        </Link>
        <span>/</span>
        <span style={{ color: "var(--color-paper)" }}>{detail.campaign_name}</span>
      </div>

      {/* Campaign Header */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: "24px",
        gap: "16px",
        flexWrap: "wrap",
        background: "rgba(255, 255, 255, 0.02)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "10px",
        padding: "20px 24px",
      }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
            <h1 style={{ fontSize: "22px", fontWeight: 590, letterSpacing: "-0.012em", color: "var(--color-paper)", margin: 0 }}>
              {detail.campaign_name}
            </h1>
            <span className={`badge ${badgeClass}`}>
              {badgeIcon(badgeClass)} {detail.status}
            </span>
          </div>

          <p style={{ color: "var(--color-fog)", fontSize: "13.5px", margin: 0 }}>
            Meta ID: <code style={{ color: "var(--color-mist)" }}>{detail.campaign_id}</code>
            {detail.ad_account_name && (
              <span style={{ marginLeft: "12px" }}>
                Cuenta: <strong style={{ color: "var(--color-paper)" }}>{detail.ad_account_name}</strong> (<code>{detail.external_account_id}</code>)
              </span>
            )}
            {detail.objective && (
              <span style={{ marginLeft: "12px" }}>
                Objetivo: <strong style={{ color: "var(--color-paper)" }}>{detail.objective}</strong>
              </span>
            )}
          </p>
        </div>

        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          {detail.ads_manager_url && (
            <a
              href={detail.ads_manager_url}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-primary btn-sm"
              style={{ display: "flex", alignItems: "center", gap: "6px", textDecoration: "none" }}
            >
              <span>Abrir en Meta Ads Manager</span>
              <span>↗</span>
            </a>
          )}
          <Link href="/campaigns" className="btn-secondary btn-sm">
            Volver
          </Link>
        </div>
      </div>

      {/* KPI Cards Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "14px", marginBottom: "28px" }}>
        <div className="glass-card" style={{ padding: "16px" }}>
          <div style={{ color: "var(--color-fog)", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Inversión Acumulada
          </div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-paper)" }}>
            {money(detail.total_spend)}
          </div>
          <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
            {fmt(detail.total_impressions)} impresiones
          </div>
        </div>

        <div className="glass-card" style={{ padding: "16px" }}>
          <div style={{ color: "var(--color-fog)", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Leads / Mensajes
          </div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-pulse-green)" }}>
            {fmt(detail.total_conversions)}
          </div>
          <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
            {fmt(detail.total_clicks)} clics
          </div>
        </div>

        <div className="glass-card" style={{ padding: "16px" }}>
          <div style={{ color: "var(--color-fog)", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            CPL Histórico Promedio
          </div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-paper)" }}>
            {money(detail.average_cpl)}
          </div>
          <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
            Costo por resultado
          </div>
        </div>

        <div className="glass-card" style={{ padding: "16px" }}>
          <div style={{ color: "var(--color-fog)", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            CTR y Frecuencia
          </div>
          <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-paper)" }}>
            {detail.average_ctr.toFixed(2)}%
          </div>
          <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
            Frecuencia: {detail.latest_frequency.toFixed(2)}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: "8px", borderBottom: "1px solid var(--border-subtle)", marginBottom: "20px" }}>
        {[
          { id: "hierarchy", label: `Estructura Meta (${detail.adsets.length} Adsets · ${detail.ads.length} Ads)` },
          { id: "history", label: `Historial Diario (${detail.history.length}d)` },
          { id: "actions", label: `Acciones y Cambios IA (${detail.actions.length})` },
          { id: "breakdowns", label: "Placements y Demografía" },
        ].map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id as any)}
            style={{
              background: "transparent",
              border: "none",
              borderBottom: activeTab === tab.id ? "2px solid var(--color-signal-teal)" : "2px solid transparent",
              color: activeTab === tab.id ? "var(--color-paper)" : "var(--color-fog)",
              padding: "10px 16px",
              fontSize: "14px",
              fontWeight: activeTab === tab.id ? 590 : 400,
              cursor: "pointer",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab 1: Hierarchy (AdSets & Ads) */}
      {activeTab === "hierarchy" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
          {/* AdSets */}
          <div className="glass-card" style={{ overflowX: "auto" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "14px", color: "var(--color-paper)" }}>
              Conjuntos de Anuncios (AdSets)
            </h3>
            {detail.adsets.length === 0 ? (
              <p style={{ color: "var(--color-fog)", fontSize: "13.5px" }}>No hay adsets registrados para esta campaña.</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "640px" }}>
                <thead>
                  <tr style={{ color: "var(--color-fog)", textAlign: "left", borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "8px" }}>Nombre del AdSet</th>
                    <th style={{ padding: "8px" }}>Presupuesto</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Gasto</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Leads</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>CPL</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>CTR</th>
                    <th style={{ padding: "8px" }}>Segmentación</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.adsets.map((as) => (
                    <tr key={as.adset_id} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <td style={{ padding: "10px 8px" }}>
                        <div style={{ fontWeight: 510, color: "var(--color-paper)" }}>{as.name}</div>
                        <div style={{ fontSize: "11px", color: "var(--color-ash)" }}><code>{as.adset_id}</code></div>
                      </td>
                      <td style={{ padding: "10px 8px" }}>{money(as.budget)}/día</td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{money(as.spend)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 510 }}>{as.conversions}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", color: "var(--color-pulse-green)" }}>{money(as.cpl)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{as.ctr.toFixed(2)}%</td>
                      <td style={{ padding: "10px 8px", fontSize: "12px", color: "var(--color-fog)" }}>
                        {as.targeting_summary?.age_range && <span>Edad: {as.targeting_summary.age_range} · </span>}
                        {as.targeting_summary?.placements && <span>Placements: {as.targeting_summary.placements.join(", ")}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Ads & Creatives */}
          <div className="glass-card" style={{ overflowX: "auto" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "14px", color: "var(--color-paper)" }}>
              Anuncios y Creativos Publicitarios (Ads)
            </h3>
            {detail.ads.length === 0 ? (
              <p style={{ color: "var(--color-fog)", fontSize: "13.5px" }}>No hay anuncios registrados para esta campaña.</p>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "16px" }}>
                {detail.ads.map((ad) => (
                  <div
                    key={ad.ad_id}
                    style={{
                      background: "rgba(255, 255, 255, 0.02)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "8px",
                      padding: "16px",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "8px" }}>
                        <h4 style={{ fontSize: "14.5px", fontWeight: 590, color: "var(--color-paper)", margin: 0 }}>
                          {ad.name}
                        </h4>
                        <span className="mono" style={{ fontSize: "11px", color: "var(--color-ash)" }}>
                          {ad.ad_id}
                        </span>
                      </div>

                      {ad.creative_title && (
                        <div style={{ fontSize: "13px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "4px" }}>
                          📌 {ad.creative_title}
                        </div>
                      )}

                      {ad.creative_body && (
                        <p style={{
                          fontSize: "12.5px",
                          color: "var(--color-fog)",
                          lineHeight: 1.5,
                          background: "rgba(0,0,0,0.2)",
                          padding: "8px 10px",
                          borderRadius: "6px",
                          marginBottom: "12px",
                          maxHeight: "90px",
                          overflowY: "auto",
                        }}>
                          {ad.creative_body}
                        </p>
                      )}
                    </div>

                    <div style={{
                      display: "flex",
                      justifyContent: "space-between",
                      borderTop: "1px solid var(--border-subtle)",
                      paddingTop: "10px",
                      fontSize: "12px",
                      color: "var(--color-ash)",
                    }}>
                      <span>Gasto: <strong style={{ color: "var(--color-paper)" }}>{money(ad.spend)}</strong></span>
                      <span>Leads: <strong style={{ color: "var(--color-pulse-green)" }}>{ad.conversions}</strong></span>
                      <span>CPL: <strong style={{ color: "var(--color-paper)" }}>{money(ad.cpl)}</strong></span>
                      <span>CTR: <strong>{ad.ctr.toFixed(2)}%</strong></span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Daily History */}
      {activeTab === "history" && (
        <div className="glass-card" style={{ padding: "1.75rem", overflowX: "auto" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "16px", color: "var(--color-paper)" }}>
            Historial Diario de Rendimiento (Snapshots de Meta)
          </h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "620px" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--color-graphite)", textAlign: "left", color: "var(--color-fog)" }}>
                <th style={{ padding: "8px" }}>Fecha</th>
                <th style={{ padding: "8px", textAlign: "right" }}>Inversión</th>
                <th style={{ padding: "8px", textAlign: "right" }}>Leads</th>
                <th style={{ padding: "8px", textAlign: "right" }}>CPL</th>
                <th style={{ padding: "8px", textAlign: "right" }}>CTR</th>
                <th style={{ padding: "8px", textAlign: "right" }}>Frecuencia</th>
              </tr>
            </thead>
            <tbody>
              {[...detail.history].reverse().map((s) => {
                const dayCpl = s.conversions > 0 ? s.spend / s.conversions : s.spend;
                return (
                  <tr key={s.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                    <td style={{ padding: "10px 8px", fontWeight: 510, color: "var(--color-paper)" }}>
                      {formatSnapshotDate(s.date)}
                    </td>
                    <td style={{ padding: "10px 8px", textAlign: "right" }}>{money(s.spend)}</td>
                    <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 510 }}>{s.conversions}</td>
                    <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 510, color: "var(--color-pulse-green)" }}>
                      {money(dayCpl)}
                    </td>
                    <td style={{ padding: "10px 8px", textAlign: "right" }}>{s.ctr.toFixed(2)}%</td>
                    <td style={{ padding: "10px 8px", textAlign: "right" }}>{s.frequency.toFixed(2)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Tab 3: Actions & Changes History */}
      {activeTab === "actions" && (
        <div className="glass-card">
          <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "16px", color: "var(--color-paper)" }}>
            Historial de Acciones y Cambios Propuestos / Ejecutados por IA
          </h3>
          {detail.actions.length === 0 ? (
            <p style={{ color: "var(--color-fog)", fontSize: "13.5px" }}>
              No se han registrado acciones automáticas ni sugerencias aprobadas para esta campaña aún.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {detail.actions.map((act) => {
                const actBadge = act.status === "COMPLETED" ? "badge-success" : act.status === "REJECTED" ? "badge-danger" : "badge-warning";
                return (
                  <div
                    key={act.id}
                    style={{
                      background: "rgba(255, 255, 255, 0.02)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "8px",
                      padding: "14px 16px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "12px",
                      flexWrap: "wrap",
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                        <span className={`badge ${actBadge}`}>{badgeIcon(actBadge)} {act.status}</span>
                        <strong style={{ color: "var(--color-paper)", fontSize: "14px" }}>{act.title}</strong>
                      </div>
                      <p style={{ fontSize: "13px", color: "var(--color-fog)", margin: "4px 0" }}>{act.description}</p>
                      {act.reject_reason && (
                        <p style={{ fontSize: "12px", color: "var(--color-coral-red)", margin: "4px 0" }}>
                          Motivo de rechazo: {act.reject_reason}
                        </p>
                      )}
                    </div>
                    <div style={{ fontSize: "12px", color: "var(--color-ash)", textAlign: "right" }}>
                      Propuesto: {new Date(act.proposed_at).toLocaleDateString()}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Breakdowns */}
      {activeTab === "breakdowns" && latest && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>
          {/* Placements Card */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "16px", color: "var(--color-paper)" }}>
              Desempeño por Placement
            </h3>
            {latest.placements_breakdown && Object.keys(latest.placements_breakdown).length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {Object.entries(latest.placements_breakdown).map(([plName, plData]: [string, any]) => (
                  <div key={plName} style={{ borderBottom: "1px solid var(--border-subtle)", paddingBottom: "0.75rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 510, marginBottom: "0.25rem" }}>
                      <span style={{ fontSize: "0.9rem", color: "var(--color-paper)" }}>
                        {plName.replace(/_/g, " ").toUpperCase()}
                      </span>
                      <span style={{ fontSize: "0.9rem", color: "var(--color-pulse-green)" }}>
                        CPL: {money(plData.cpl)}
                      </span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--color-ash)" }}>
                      <span>Inversión: {money(plData.spend)}</span>
                      <span>Leads: {plData.conversions || 0}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--color-fog)", fontSize: "13.5px" }}>No hay información de desglose por placement.</p>
            )}
          </div>

          {/* Demographics Card */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "16px", color: "var(--color-paper)" }}>
              Edad y Segmentación
            </h3>
            {latest.demographics_breakdown && Object.keys(latest.demographics_breakdown).length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {Object.entries(latest.demographics_breakdown).map(([ageRange, ageData]: [string, any]) => (
                  <div key={ageRange} style={{ borderBottom: "1px solid var(--border-subtle)", paddingBottom: "0.75rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 510, marginBottom: "0.25rem" }}>
                      <span style={{ fontSize: "0.9rem", color: "var(--color-paper)" }}>Rango: {ageRange}</span>
                      <span style={{ fontSize: "0.9rem", color: "var(--color-pulse-green)" }}>Leads: {ageData.conversions || 0}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "var(--color-ash)" }}>
                      <span>Inversión: {money(ageData.spend)}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--color-fog)", fontSize: "13.5px" }}>No hay información de desglose demográfico.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
