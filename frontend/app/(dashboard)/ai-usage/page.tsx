"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AiUsage } from "@/lib/types";

// Nombres legibles por funcionalidad (la `feature` cruda que graba el backend).
const FEATURE_LABELS: Record<string, string> = {
  master_brief: "Reporte / Master Brief",
  campaign_copy: "Copy al crear campaña",
  campaign_copy_action: "Copy en acción sugerida",
  campaign_analysis: "Análisis de campaña",
};

const RANGES = [7, 30, 90];

function fmt(n: number) {
  return n.toLocaleString("es-PE");
}

export default function AiUsagePage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<AiUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    api.getAiUsage(days)
      .then((d: AiUsage) => setData(d))
      .catch((e: any) => setError(e.message || "No se pudo cargar el consumo de IA."))
      .finally(() => setLoading(false));
  }, [days]);

  const t = data?.totals;

  return (
    <div style={{ maxWidth: "960px", margin: "32px auto 0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", marginBottom: "8px" }}>
        <h1 style={{ fontSize: "20px", fontWeight: 590, letterSpacing: "-0.012em" }}>Uso de IA</h1>
        <select className="form-input" value={days} onChange={(e) => setDays(Number(e.target.value))}
          style={{ width: "auto", background: "var(--color-obsidian)" }}>
          {RANGES.map((r) => <option key={r} value={r}>Últimos {r} días</option>)}
        </select>
      </div>
      <p style={{ color: "var(--color-fog)", fontSize: "14px", marginBottom: "24px" }}>
        Tokens que se envían y devuelve la IA, desglosados por funcionalidad, con costo estimado. Solo
        cuenta llamadas reales al modelo (en modo simulado no hay consumo).
      </p>

      {error && (
        <div role="alert" style={{
          background: "rgba(235, 87, 87, 0.1)", border: "1px solid rgba(235, 87, 87, 0.2)",
          color: "var(--color-coral-red)", padding: "12px 16px", borderRadius: "6px", marginBottom: "16px", fontSize: "14px",
        }}>{error}</div>
      )}

      {loading ? (
        <p style={{ color: "var(--color-fog)" }}>Cargando...</p>
      ) : !data || data.by_feature.length === 0 ? (
        <div className="glass-card">
          <p style={{ color: "var(--color-fog)" }}>
            Todavía no hay consumo real de IA registrado en este periodo. Aparecerá aquí en cuanto se
            generen reportes o copys de campaña con un modelo real conectado.
          </p>
        </div>
      ) : (
        <>
          {/* Tarjetas de totales */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "12px", marginBottom: "24px" }}>
            {[
              { label: "Tokens totales", value: fmt(t!.total_tokens) },
              { label: "Costo estimado", value: `$${t!.estimated_cost_usd.toFixed(4)}` },
              { label: "Llamadas", value: fmt(t!.calls) },
              { label: "Con cadena de pensamiento", value: `${fmt(t!.cot_calls)} / ${fmt(t!.calls)}` },
            ].map((kpi) => (
              <div key={kpi.label} className="glass-card" style={{ padding: "16px" }}>
                <div style={{ fontSize: "12px", color: "var(--color-fog)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{kpi.label}</div>
                <div style={{ fontSize: "22px", fontWeight: 590, marginTop: "6px" }}>{kpi.value}</div>
              </div>
            ))}
          </div>

          {/* Desglose por funcionalidad */}
          <div className="glass-card" style={{ overflowX: "auto" }}>
            <h2 style={{ fontSize: "15px", fontWeight: 560, marginBottom: "12px" }}>Desglose por funcionalidad</h2>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "620px" }}>
              <thead>
                <tr style={{ color: "var(--color-fog)", textAlign: "left", borderBottom: "1px solid var(--color-graphite)" }}>
                  <th style={{ padding: "8px" }}>Funcionalidad</th>
                  <th style={{ padding: "8px", textAlign: "right" }}>Llamadas</th>
                  <th style={{ padding: "8px", textAlign: "right" }}>Prompt</th>
                  <th style={{ padding: "8px", textAlign: "right" }}>Respuesta</th>
                  <th style={{ padding: "8px", textAlign: "right" }}>Razonamiento</th>
                  <th style={{ padding: "8px", textAlign: "right" }}>Total</th>
                  <th style={{ padding: "8px", textAlign: "right" }}>Costo est.</th>
                </tr>
              </thead>
              <tbody>
                {data.by_feature.map((f) => (
                  <tr key={f.feature} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                    <td style={{ padding: "8px" }}>
                      {FEATURE_LABELS[f.feature] || f.feature}
                      {f.cot_calls > 0 && (
                        <span title="Usó cadena de pensamiento" style={{ marginLeft: "6px", fontSize: "11px", color: "var(--color-mist)" }}>🧠</span>
                      )}
                    </td>
                    <td style={{ padding: "8px", textAlign: "right" }}>{fmt(f.calls)}</td>
                    <td style={{ padding: "8px", textAlign: "right" }}>{fmt(f.prompt_tokens)}</td>
                    <td style={{ padding: "8px", textAlign: "right" }}>{fmt(f.completion_tokens)}</td>
                    <td style={{ padding: "8px", textAlign: "right" }}>{fmt(f.reasoning_tokens)}</td>
                    <td style={{ padding: "8px", textAlign: "right", fontWeight: 560 }}>{fmt(f.total_tokens)}</td>
                    <td style={{ padding: "8px", textAlign: "right" }}>${f.estimated_cost_usd.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
