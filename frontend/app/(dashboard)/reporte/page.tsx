"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { useWebSocket } from "@/components/providers/WebSocketProvider";

const card: React.CSSProperties = {
  background: "var(--color-carbon)",
  borderRadius: "12px",
  padding: "24px",
  boxShadow: "var(--shadow-subtle)",
};
const th: React.CSSProperties = { padding: "0.6rem 0.75rem", textAlign: "left", color: "var(--color-fog)", fontSize: "12px", fontWeight: 510, borderBottom: "1px solid var(--color-graphite)" };
const td: React.CSSProperties = { padding: "0.6rem 0.75rem", borderBottom: "1px solid var(--border-color)", fontSize: "0.85rem" };
const h2: React.CSSProperties = { fontSize: "16px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "1rem" };

const labelBadge = (label: string) =>
  label === "Ganadora" || label === "Buena" ? "badge-success" : label === "Regular" ? "badge-warning" : "badge-danger";

function money(n: number) { return `S/.${(n ?? 0).toFixed(2)}`; }
function num(n: number) { return (n ?? 0).toLocaleString(); }

function Kpi({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="card-subtle" style={{ textAlign: "center" }}>
      <div style={{ fontSize: "12px", color: "var(--color-fog)" }}>{label}</div>
      <div style={{ fontSize: "20px", fontWeight: 590, marginTop: "0.35rem", letterSpacing: "-0.012em", color: highlight ? "var(--color-pulse-green)" : "var(--color-paper)" }}>{value}</div>
    </div>
  );
}

function BreakdownTable({ title, data }: { title: string; data: Record<string, any> }) {
  const rows = Object.entries(data || {});
  if (rows.length === 0) return null;
  // min-width: 0 es necesario porque los ítems de un grid no se encogen por
  // debajo de su contenido mínimo por defecto — con nombres largos y sin
  // espacios (ej. "facebook_facebook_reels_overlay") esto hacía que la tabla
  // se saliera de su columna y se superpusiera con la siguiente.
  // Columnas con ancho proporcional fijo: en table-layout:fixed sin esto, el
  // ancho de cada columna se toma de la primera fila y queda impredecible.
  const segCell: React.CSSProperties = { ...td, width: "40%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
  return (
    <div style={{ minWidth: 0 }}>
      <h4 style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "0.5rem" }}>{title}</h4>
      <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
        <thead><tr><th style={{ ...th, width: "40%" }}>Segmento</th><th style={{ ...th, width: "20%" }}>Gasto</th><th style={{ ...th, width: "15%" }}>Conv.</th><th style={{ ...th, width: "25%" }}>Costo/Conv.</th></tr></thead>
        <tbody>
          {rows.map(([k, v]: [string, any]) => {
            const conv = v.conversions || 0;
            const cost = conv > 0 ? v.spend / conv : v.spend;
            return (
              <tr key={k}>
                <td style={segCell} title={k.replace(/_/g, " ")}>{k.replace(/_/g, " ")}</td>
                <td style={td}>{money(v.spend)}</td>
                <td style={td}>{conv}</td>
                <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{money(cost)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const BAR_COLORS = ["#6366f1", "#02b8cc", "#27a644", "#8b5cf6", "#f2994a", "#eb5757"];

function DistributionBars({ title, data }: { title: string; data: Record<string, any> }) {
  const rows = Object.entries(data || {});
  const total = rows.reduce((acc, [, v]: [string, any]) => acc + (v.spend || 0), 0);
  if (total <= 0) return null;
  const sorted = rows.sort((a, b) => (b[1].spend || 0) - (a[1].spend || 0));
  return (
    <div>
      <h4 style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "0.75rem" }}>{title}</h4>
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {sorted.map(([k, v]: [string, any], i: number) => {
          const pct = Math.round((v.spend / total) * 100);
          return (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={{ fontSize: "12px", color: "var(--text-secondary)", width: "150px", flexShrink: 0 }}>{k.replace(/_/g, " ")}</span>
              <div style={{ flex: 1, background: "var(--color-graphite)", borderRadius: "4px", height: "8px", overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "8px", background: BAR_COLORS[i % BAR_COLORS.length], borderRadius: "4px" }} />
              </div>
              <span style={{ fontSize: "12px", fontWeight: 510, color: "var(--color-paper)", width: "44px", textAlign: "right" }}>{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Reporte() {
  const { lastEvent } = useWebSocket();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncMessage, setSyncMessage] = useState("");

  const fetchReport = async () => {
    try {
      setData(await api.getBriefData());
      setError("");
    } catch (e: any) {
      setError(e?.message || "No se pudo cargar el reporte.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReport();
  }, []);

  // Sin esto la página se quedaba con el reporte de antes de sincronizar: el
  // sync actualiza KPIs/campañas/segmentación, y el análisis de IA (encadenado
  // automáticamente después) actualiza la narrativa — hay que refrescar en
  // ambos, no solo uno.
  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.event_type === "sync_status" && lastEvent.status === "COMPLETED") {
      setSyncMessage("Sincronización completada — actualizando reporte...");
      fetchReport();
    } else if (lastEvent.event_type === "analysis_status" && lastEvent.status === "COMPLETED") {
      setSyncMessage("Diagnóstico de IA actualizado.");
      fetchReport();
    }
  }, [lastEvent]);

  if (loading) return <div style={{ color: "var(--text-secondary)" }}>Cargando reporte…</div>;

  if (error || !data?.report) {
    return (
      <div style={{ ...card, textAlign: "center", padding: "3rem" }}>
<h3 style={{ color: "var(--color-paper)", fontSize: "18px", fontWeight: 590, marginBottom: "0.5rem" }}>Aún no hay datos para el reporte</h3>
        <p style={{ color: "var(--text-secondary)", maxWidth: 460, margin: "0 auto 1rem" }}>
          {error || "Sincroniza tu cuenta para generar el reporte de rendimiento."}
        </p>
        <div style={{ display: "flex", gap: "0.75rem", justifyContent: "center" }}>
          <Link href="/" className="btn-primary">Ir a sincronizar</Link>
          <Link href="/settings" className="btn-secondary">Configuración</Link>
        </div>
      </div>
    );
  }

  const r = data.report;
  const k = r.kpis;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Reporte de Rendimiento</h1>
          <p style={{ color: "var(--color-fog)", fontSize: "14px", marginTop: "4px" }}>
            {data.ad_account_name} · <span className="mono">{r.account.external_account_id}</span> · últimos {r.window_days} días
          </p>
          {syncMessage && (
            <p className="mono" style={{ color: "var(--color-signal-teal)", fontSize: "13px", marginTop: "8px" }}>{syncMessage}</p>
          )}
        </div>
        <button onClick={() => window.print()} className="btn-secondary no-print">
          Exportar PDF
        </button>
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

      {/* Hallazgos y recomendaciones (P-2.7) */}
      {(r.findings?.length > 0 || r.recommendations?.length > 0) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
          {r.findings?.length > 0 && (
            <div style={{ ...card, borderLeft: "2px solid var(--color-amber)" }}>
              <h2 style={{ ...h2, fontSize: "15px" }}>Lo más importante que encontramos</h2>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "8px" }}>
                {r.findings.map((f: string, i: number) => (
                  <li key={i} style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5, paddingLeft: "14px", position: "relative" }}>
                    <span style={{ position: "absolute", left: 0, color: "var(--color-amber)" }}>→</span>{f}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.recommendations?.length > 0 && (
            <div style={{ ...card, borderLeft: "2px solid var(--color-pulse-green)" }}>
              <h2 style={{ ...h2, fontSize: "15px" }}>Qué hacer con las nuevas campañas</h2>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "8px" }}>
                {r.recommendations.map((rec: string, i: number) => (
                  <li key={i} style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5, paddingLeft: "14px", position: "relative" }}>
                    <span style={{ position: "absolute", left: 0, color: "var(--color-pulse-green)" }}>✓</span>{rec}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Alertas */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
        <div style={{ ...card, borderLeft: "2px solid var(--color-coral-red)" }}>
          <h2 style={{ ...h2, fontSize: "15px" }}>Revisar / pausar</h2>
          {r.alerts.red.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Sin campañas críticas.</p> :
            r.alerts.red.map((a: any, i: number) => (
              <p key={i} style={{ fontSize: "0.85rem", marginBottom: "0.5rem" }}><strong>{a.name}:</strong> {a.reason}</p>
            ))}
        </div>
        <div style={{ ...card, borderLeft: "2px solid var(--color-pulse-green)" }}>
          <h2 style={{ ...h2, fontSize: "15px" }}>Ganadoras — replicar</h2>
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
                <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{c.name}</td>
                <td style={td}>{money(c.spend)}</td>
                <td style={td}>{num(c.reach)}</td>
                <td style={td}>{c.frequency}</td>
                <td style={td}>{c.ctr}%</td>
                <td style={td}>{c.conversations}</td>
                <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{money(c.cost_per_conv)}</td>
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
                  <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{a.name}</td>
                  <td style={{ ...td, color: "var(--text-secondary)", fontSize: "0.78rem" }}>
                    {a.targeting?.advantage_audience ? "Advantage+" : "Intereses"} · {(a.targeting?.geo || []).join(", ")}
                  </td>
                  <td style={td}>{money(a.spend)}</td>
                  <td style={td}>{num(a.reach)}</td>
                  <td style={td}>{a.ctr}%</td>
                  <td style={td}>{a.conversations}</td>
                  <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{money(a.cost_per_conv)}</td>
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
                  <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{a.name}</td>
                  <td style={td}>{money(a.spend)}</td>
                  <td style={td}>{num(a.reach)}</td>
                  <td style={td}>{num(a.impressions)}</td>
                  <td style={td}>{a.ctr}%</td>
                  <td style={td}>{a.conversations}</td>
                  <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{money(a.cost_per_conv)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Segmentación */}
      <div style={card}>
        <h2 style={h2}>¿Dónde convierte mejor? — Plataforma y Dispositivo</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "1.5rem", marginBottom: "1.5rem" }}>
          <DistributionBars title="Distribución de gasto por placement" data={r.segmentation.placements} />
          <DistributionBars title="Distribución de gasto por dispositivo" data={r.segmentation.device} />
        </div>
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
          <h2 style={{ ...h2, fontSize: "15px" }}>Audiencias personalizadas</h2>
          {r.audiences.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Esta cuenta no tiene audiencias personalizadas guardadas en Meta (usa Advantage+ o segmentación por intereses).</p> :
            r.audiences.map((a: any, i: number) => (
              <div key={i} style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border-color)" }}>
                <strong style={{ color: "var(--color-paper)", fontWeight: 510, fontSize: "14px" }}>{a.name}</strong>{" "}
                <span className="badge badge-success">{a.subtype}</span>
                <p style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>{a.description} · ~{num(a.approximate_count)}</p>
              </div>
            ))}
        </div>
        <div style={card}>
          <h2 style={{ ...h2, fontSize: "15px" }}>Inventario de creativos</h2>
          {r.creatives.length === 0 ? <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Sin creativos.</p> :
            r.creatives.map((c: any, i: number) => (
              <div key={i} style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--border-color)", fontSize: "0.85rem" }}>
                <strong style={{ color: "var(--color-paper)", fontWeight: 510 }}>{c.name || c.image_hash}</strong>
                <span style={{ color: "var(--text-secondary)" }}> · {c.width}×{c.height}</span>
              </div>
            ))}
        </div>
      </div>

      {/* Parámetros óptimos */}
      <div style={{ ...card, borderLeft: "2px solid var(--color-pulse-green)" }}>
        <h2 style={h2}>Parámetros Óptimos para Nuevas Campañas</h2>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={th}>Parámetro</th><th style={th}>Valor recomendado</th><th style={th}>Justificación</th></tr></thead>
          <tbody>
            {r.optimal_params.map((p: any, i: number) => (
              <tr key={i}>
                <td style={{ ...td, fontWeight: 510, color: "var(--color-paper)" }}>{p.param}</td>
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
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "0.75rem" }}>
            <h2 style={{ ...h2, marginBottom: 0 }}>Diagnóstico de la IA</h2>
            {/* C-4: si el LLM no estuvo disponible, la narrativa salió de una
                plantilla local — no se presenta como "Procesado por IA". */}
            {data.narrative_provider === "fallback_template" ? (
              <span className="badge badge-warning" title="El servicio de IA no estuvo disponible; este es un informe básico generado con los datos reales de la cuenta, sin análisis del modelo.">
                Informe básico — IA no disponible
              </span>
            ) : (
              <span className="badge badge-ai">Procesado por IA</span>
            )}
          </div>
          <div className="narrative-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.narrative}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
