"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

const card: React.CSSProperties = {
  background: "var(--card-bg)",
  border: "1px solid var(--border-color)",
  borderRadius: "16px",
  padding: "1.5rem",
  boxShadow: "0 8px 32px 0 rgba(0,0,0,0.3)",
};
const th: React.CSSProperties = { padding: "0.6rem 0.75rem", textAlign: "left", color: "var(--text-secondary)", fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.04em", borderBottom: "1px solid var(--border-color)" };
const td: React.CSSProperties = { padding: "0.6rem 0.75rem", borderBottom: "1px solid var(--border-color)", fontSize: "0.85rem" };
const h2: React.CSSProperties = { fontSize: "1.15rem", fontWeight: 700, color: "#fff", marginBottom: "1rem" };

const labelBadge = (label: string) =>
  label === "Ganadora" || label === "Buena" ? "badge-success" : label === "Regular" ? "badge-warning" : "badge-danger";

function money(n: number) { return `S/.${(n ?? 0).toFixed(2)}`; }
function num(n: number) { return (n ?? 0).toLocaleString(); }

function Kpi({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid var(--border-color)", borderRadius: "12px", padding: "1rem", textAlign: "center" }}>
      <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: "1.6rem", fontWeight: 800, marginTop: "0.35rem", color: highlight ? "var(--success)" : "#fff" }}>{value}</div>
    </div>
  );
}

function BreakdownTable({ title, data }: { title: string; data: Record<string, any> }) {
  const rows = Object.entries(data || {});
  if (rows.length === 0) return null;
  return (
    <div>
      <h4 style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "0.5rem" }}>{title}</h4>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead><tr><th style={th}>Segmento</th><th style={th}>Gasto</th><th style={th}>Conv.</th><th style={th}>Costo/Conv.</th></tr></thead>
        <tbody>
          {rows.map(([k, v]: [string, any]) => {
            const conv = v.conversions || 0;
            const cost = conv > 0 ? v.spend / conv : v.spend;
            return (
              <tr key={k}>
                <td style={td}>{k}</td>
                <td style={td}>{money(v.spend)}</td>
                <td style={td}>{conv}</td>
                <td style={{ ...td, fontWeight: 700 }}>{money(cost)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Reporte() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        setData(await api.getBriefData());
      } catch (e: any) {
        setError(e?.message || "No se pudo cargar el reporte.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div style={{ color: "var(--text-secondary)" }}>Cargando reporte…</div>;

  if (error || !data?.report) {
    return (
      <div style={{ ...card, textAlign: "center", padding: "3rem" }}>
        <div style={{ fontSize: "2.5rem", marginBottom: "0.75rem" }}>📄</div>
        <h3 style={{ color: "#fff", fontSize: "1.2rem", marginBottom: "0.5rem" }}>Aún no hay datos para el reporte</h3>
        <p style={{ color: "var(--text-secondary)", maxWidth: 460, margin: "0 auto 1rem" }}>
          {error || "Sincroniza tu cuenta para generar el reporte de rendimiento."}
        </p>
        <div style={{ display: "flex", gap: "0.75rem", justifyContent: "center" }}>
          <Link href="/" className="btn-primary" style={{ padding: "0.5rem 1.25rem" }}>Ir a sincronizar</Link>
          <Link href="/settings" className="btn-secondary" style={{ padding: "0.5rem 1.25rem" }}>Configuración</Link>
        </div>
      </div>
    );
  }

  const r = data.report;
  const k = r.kpis;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Header */}
      <div>
        <h1 style={{ fontSize: "1.9rem", fontWeight: 800 }}>Reporte de Rendimiento</h1>
        <p style={{ color: "var(--text-secondary)" }}>
          {data.ad_account_name} · {r.account.external_account_id} · últimos {r.window_days} días
        </p>
      </div>

      {/* KPIs */}
      <div style={card}>
        <h2 style={h2}>Resumen Ejecutivo</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "1rem" }}>
          <Kpi label="Total invertido" value={money(k.total_spend)} />
          <Kpi label="Personas alcanzadas" value={num(k.total_reach)} />
          <Kpi label="Conversaciones" value={num(k.total_conversations)} />
          <Kpi label="Mejor costo/conv." value={money(k.best_cost_per_conv)} highlight />
          <Kpi label="CTR promedio" value={`${(k.avg_ctr ?? 0).toFixed(2)}%`} />
        </div>
      </div>

      {/* Alertas */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
        <div style={{ ...card, borderLeft: "4px solid var(--danger)" }}>
          <h2 style={{ ...h2, fontSize: "1rem" }}>⚠️ Revisar / pausar</h2>
          {r.alerts.red.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Sin campañas críticas.</p> :
            r.alerts.red.map((a: any, i: number) => (
              <p key={i} style={{ fontSize: "0.85rem", marginBottom: "0.5rem" }}><strong>{a.name}:</strong> {a.reason}</p>
            ))}
        </div>
        <div style={{ ...card, borderLeft: "4px solid var(--success)" }}>
          <h2 style={{ ...h2, fontSize: "1rem" }}>🏆 Ganadoras — replicar</h2>
          {r.alerts.green.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Sin campañas destacadas aún.</p> :
            r.alerts.green.map((a: any, i: number) => (
              <p key={i} style={{ fontSize: "0.85rem", marginBottom: "0.5rem" }}><strong>{a.name}:</strong> {a.reason}</p>
            ))}
        </div>
      </div>

      {/* Campañas */}
      <div style={{ ...card, overflowX: "auto" }}>
        <h2 style={h2}>Rendimiento por Campaña <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)", fontWeight: 400 }}>· ordenado por costo/conversación</span></h2>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr>
            <th style={th}>Campaña</th><th style={th}>Gasto</th><th style={th}>Alcance</th><th style={th}>Frec.</th>
            <th style={th}>CTR</th><th style={th}>Conv.</th><th style={th}>Costo/Conv.</th><th style={th}>Estado</th>
          </tr></thead>
          <tbody>
            {r.campaigns.map((c: any) => (
              <tr key={c.campaign_id}>
                <td style={{ ...td, fontWeight: 600, color: "#fff" }}>{c.name}</td>
                <td style={td}>{money(c.spend)}</td>
                <td style={td}>{num(c.reach)}</td>
                <td style={td}>{c.frequency}</td>
                <td style={td}>{c.ctr}%</td>
                <td style={td}>{c.conversations}</td>
                <td style={{ ...td, fontWeight: 700 }}>{money(c.cost_per_conv)}</td>
                <td style={td}><span className={`badge ${labelBadge(c.label)}`}>{c.label}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Adsets */}
      {r.adsets.length > 0 && (
        <div style={{ ...card, overflowX: "auto" }}>
          <h2 style={h2}>Rendimiento por Conjunto de Anuncios <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)", fontWeight: 400 }}>· nivel adset</span></h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>Adset</th><th style={th}>Targeting</th><th style={th}>Gasto</th>
              <th style={th}>Alcance</th><th style={th}>CTR</th><th style={th}>Conv.</th><th style={th}>Costo/Conv.</th>
            </tr></thead>
            <tbody>
              {r.adsets.map((a: any) => (
                <tr key={a.adset_id}>
                  <td style={{ ...td, fontWeight: 600, color: "#fff" }}>{a.name}</td>
                  <td style={{ ...td, color: "var(--text-secondary)", fontSize: "0.78rem" }}>
                    {a.targeting?.advantage_audience ? "Advantage+" : "Intereses"} · {(a.targeting?.geo || []).join(", ")}
                  </td>
                  <td style={td}>{money(a.spend)}</td>
                  <td style={td}>{num(a.reach)}</td>
                  <td style={td}>{a.ctr}%</td>
                  <td style={td}>{a.conversations}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{money(a.cost_per_conv)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Anuncios / creativos */}
      {r.ads.length > 0 && (
        <div style={{ ...card, overflowX: "auto" }}>
          <h2 style={h2}>Rendimiento por Anuncio / Creativo</h2>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>Anuncio</th><th style={th}>Gasto</th><th style={th}>Alcance</th>
              <th style={th}>Impr.</th><th style={th}>CTR</th><th style={th}>Conv.</th><th style={th}>Costo/Conv.</th>
            </tr></thead>
            <tbody>
              {r.ads.map((a: any) => (
                <tr key={a.ad_id}>
                  <td style={{ ...td, fontWeight: 600, color: "#fff" }}>{a.name}</td>
                  <td style={td}>{money(a.spend)}</td>
                  <td style={td}>{num(a.reach)}</td>
                  <td style={td}>{num(a.impressions)}</td>
                  <td style={td}>{a.ctr}%</td>
                  <td style={td}>{a.conversations}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{money(a.cost_per_conv)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Segmentación */}
      <div style={card}>
        <h2 style={h2}>¿Dónde convierte mejor? — Segmentación</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "1.5rem" }}>
          <BreakdownTable title="Plataforma / Placement" data={r.segmentation.placements} />
          <BreakdownTable title="Dispositivo" data={r.segmentation.device} />
          <BreakdownTable title="Edad" data={r.segmentation.age} />
          <BreakdownTable title="Género" data={r.segmentation.gender} />
          <BreakdownTable title="Geografía" data={r.segmentation.region} />
        </div>
      </div>

      {/* Audiencias + creativos */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
        <div style={card}>
          <h2 style={{ ...h2, fontSize: "1rem" }}>Audiencias personalizadas</h2>
          {r.audiences.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Sin audiencias.</p> :
            r.audiences.map((a: any, i: number) => (
              <div key={i} style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border-color)" }}>
                <strong style={{ color: "#fff", fontSize: "0.88rem" }}>{a.name}</strong>{" "}
                <span className="badge badge-success" style={{ fontSize: "0.62rem" }}>{a.subtype}</span>
                <p style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>{a.description} · ~{num(a.approximate_count)}</p>
              </div>
            ))}
        </div>
        <div style={card}>
          <h2 style={{ ...h2, fontSize: "1rem" }}>Inventario de creativos</h2>
          {r.creatives.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Sin creativos.</p> :
            r.creatives.map((c: any, i: number) => (
              <div key={i} style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border-color)", fontSize: "0.85rem" }}>
                <strong style={{ color: "#fff" }}>{c.name || c.image_hash}</strong>
                <span style={{ color: "var(--text-secondary)" }}> · {c.width}×{c.height}</span>
              </div>
            ))}
        </div>
      </div>

      {/* Parámetros óptimos */}
      <div style={{ ...card, borderLeft: "4px solid var(--success)" }}>
        <h2 style={h2}>Parámetros Óptimos para Nuevas Campañas</h2>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={th}>Parámetro</th><th style={th}>Valor recomendado</th><th style={th}>Justificación</th></tr></thead>
          <tbody>
            {r.optimal_params.map((p: any, i: number) => (
              <tr key={i}>
                <td style={{ ...td, fontWeight: 700, color: "#fff" }}>{p.param}</td>
                <td style={td}>{p.value}</td>
                <td style={{ ...td, color: "var(--text-secondary)", fontSize: "0.8rem" }}>{p.justification}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Narrativa IA (si existe) */}
      {data.narrative && (
        <div style={card}>
          <h2 style={h2}>🧠 Diagnóstico de la IA</h2>
          <div style={{ whiteSpace: "pre-line", color: "var(--text-primary)", lineHeight: 1.6, fontSize: "0.9rem" }}>{data.narrative}</div>
        </div>
      )}
    </div>
  );
}
