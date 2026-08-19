"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAdAccount } from "@/components/providers/AdAccountProvider";
import type { AiUsage } from "@/lib/types";

const FEATURE_LABELS: Record<string, { label: string; icon: string }> = {
  master_brief: { label: "Reporte Ejecutivo / Master Brief", icon: "📊" },
  campaign_copy: { label: "Generación de Copy (Creación)", icon: "✍️" },
  campaign_copy_action: { label: "Copy en Acción Sugerida", icon: "⚡" },
  campaign_analysis: { label: "Análisis y Diagnóstico de Campañas", icon: "🔍" },
  chat_query: { label: "Chat Analista / Consultas", icon: "💬" },
  chat_build: { label: "Chat Constructor de Campañas", icon: "🛠️" },
  optimizer_action: { label: "Motor de Decisiones de Optimización", icon: "🤖" },
};

const RANGES = [
  { label: "Últimos 7 días", value: 7 },
  { label: "Últimos 30 días", value: 30 },
  { label: "Últimos 90 días", value: 90 },
  { label: "Último año (365d)", value: 365 },
];

function fmt(n: number) {
  return (n || 0).toLocaleString("es-PE");
}

export default function AiUsagePage() {
  const { accounts } = useAdAccount();
  const [days, setDays] = useState(30);
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"features" | "models" | "accounts" | "timeline">("features");
  const [data, setData] = useState<AiUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    api.getAiUsage(days, selectedAccountId || undefined)
      .then((d: AiUsage) => setData(d))
      .catch((e: any) => setError(e.message || "No se pudo cargar el consumo de IA."))
      .finally(() => setLoading(false));
  }, [days, selectedAccountId]);

  const t = data?.totals;

  return (
    <div style={{ maxWidth: "1080px", margin: "32px auto 0 auto" }}>
      {/* Header & Filters */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px", flexWrap: "wrap", marginBottom: "16px" }}>
        <div>
          <h1 style={{ fontSize: "22px", fontWeight: 590, letterSpacing: "-0.012em", color: "var(--color-paper)" }}>
            Consumo y Auditoría de IA
          </h1>
          <p style={{ color: "var(--color-fog)", fontSize: "14px", marginTop: "4px" }}>
            Transparencia total de tokens consumidos, llamadas a modelos y costo estimado en USD en tiempo real.
          </p>
        </div>

        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          {/* Account Filter */}
          <select
            className="form-input"
            value={selectedAccountId}
            onChange={(e) => setSelectedAccountId(e.target.value)}
            style={{ width: "auto", background: "var(--color-obsidian)" }}
          >
            <option value="">Todas las cuentas</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>

          {/* Date Range Filter */}
          <select
            className="form-input"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            style={{ width: "auto", background: "var(--color-obsidian)" }}
          >
            {RANGES.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div role="alert" style={{
          background: "rgba(235, 87, 87, 0.1)", border: "1px solid rgba(235, 87, 87, 0.2)",
          color: "var(--color-coral-red)", padding: "12px 16px", borderRadius: "6px", marginBottom: "16px", fontSize: "14px",
        }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="glass-card" style={{ textAlign: "center", padding: "3rem" }}>
          <p style={{ color: "var(--color-fog)" }}>Cargando métricas de consumo de IA...</p>
        </div>
      ) : !data || data.totals.calls === 0 ? (
        <div className="glass-card" style={{ textAlign: "center", padding: "3rem" }}>
          <span style={{ fontSize: "32px", display: "block", marginBottom: "12px" }}>🤖</span>
          <h3 style={{ fontSize: "16px", fontWeight: 590, marginBottom: "8px", color: "var(--color-paper)" }}>
            Sin consumo registrado en este periodo
          </h3>
          <p style={{ color: "var(--color-fog)", fontSize: "14px", maxWidth: "480px", margin: "0 auto" }}>
            Las llamadas a modelos reales de IA (OpenAI, Gemini, Anthropic) aparecerán aquí desglosadas por funcionalidad, modelo y costo en cuanto se generen reportes, diagnósticos o copys.
          </p>
        </div>
      ) : (
        <>
          {/* Summary KPIs */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px", marginBottom: "24px" }}>
            <div className="glass-card" style={{ padding: "18px" }}>
              <div style={{ fontSize: "12px", color: "var(--color-fog)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                Tokens Totales
              </div>
              <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-paper)" }}>
                {fmt(t!.total_tokens)}
              </div>
              <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
                Prompt: {fmt(t!.prompt_tokens)} · Res: {fmt(t!.completion_tokens)}
              </div>
            </div>

            <div className="glass-card" style={{ padding: "18px" }}>
              <div style={{ fontSize: "12px", color: "var(--color-fog)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                Costo Estimado USD
              </div>
              <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-pulse-green)" }}>
                ${t!.estimated_cost_usd.toFixed(4)}
              </div>
              <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
                Basado en precios oficiales por token
              </div>
            </div>

            <div className="glass-card" style={{ padding: "18px" }}>
              <div style={{ fontSize: "12px", color: "var(--color-fog)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                Llamadas a Modelos
              </div>
              <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-paper)" }}>
                {fmt(t!.calls)}
              </div>
              <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
                {fmt(t!.cot_calls)} con razonamiento (CoT)
              </div>
            </div>

            <div className="glass-card" style={{ padding: "18px" }}>
              <div style={{ fontSize: "12px", color: "var(--color-fog)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                Promedio por Llamada
              </div>
              <div style={{ fontSize: "24px", fontWeight: 590, marginTop: "6px", color: "var(--color-paper)" }}>
                {t!.calls > 0 ? fmt(Math.round(t!.total_tokens / t!.calls)) : 0} <span style={{ fontSize: "14px", fontWeight: 400 }}>tok</span>
              </div>
              <div style={{ fontSize: "12px", color: "var(--color-ash)", marginTop: "4px" }}>
                ${t!.calls > 0 ? (t!.estimated_cost_usd / t!.calls).toFixed(5) : 0} USD/call
              </div>
            </div>
          </div>

          {/* Navigation Tabs */}
          <div style={{ display: "flex", gap: "8px", borderBottom: "1px solid var(--border-subtle)", marginBottom: "20px" }}>
            {[
              { id: "features", label: "Por Funcionalidad" },
              { id: "models", label: "Por Modelo / Proveedor" },
              { id: "accounts", label: "Por Cuenta Publicitaria" },
              { id: "timeline", label: "Línea de Tiempo Diaria" },
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

          {/* Tab 1: By Feature */}
          {activeTab === "features" && (
            <div className="glass-card" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "680px" }}>
                <thead>
                  <tr style={{ color: "var(--color-fog)", textAlign: "left", borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "10px 8px" }}>Operación / Funcionalidad</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Llamadas</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Prompt</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Respuesta</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Razonamiento</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Total Tokens</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Costo Est.</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_feature.map((f) => {
                    const meta = FEATURE_LABELS[f.feature] || { label: f.feature, icon: "⚙️" };
                    return (
                      <tr key={f.feature} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                        <td style={{ padding: "10px 8px", display: "flex", alignItems: "center", gap: "8px" }}>
                          <span>{meta.icon}</span>
                          <span style={{ fontWeight: 510, color: "var(--color-paper)" }}>{meta.label}</span>
                          {f.cot_calls > 0 && (
                            <span title={`${f.cot_calls} llamadas con cadena de pensamiento`} style={{ fontSize: "11px", color: "var(--color-signal-teal)" }}>
                              🧠 CoT
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(f.calls)}</td>
                        <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(f.prompt_tokens)}</td>
                        <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(f.completion_tokens)}</td>
                        <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(f.reasoning_tokens)}</td>
                        <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 590, color: "var(--color-paper)" }}>
                          {fmt(f.total_tokens)}
                        </td>
                        <td style={{ padding: "10px 8px", textAlign: "right", color: "var(--color-pulse-green)", fontWeight: 510 }}>
                          ${f.estimated_cost_usd.toFixed(4)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Tab 2: By Model */}
          {activeTab === "models" && (
            <div className="glass-card" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "620px" }}>
                <thead>
                  <tr style={{ color: "var(--color-fog)", textAlign: "left", borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "10px 8px" }}>Proveedor</th>
                    <th style={{ padding: "10px 8px" }}>Modelo LLM</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Llamadas</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Prompt Tokens</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Completion Tokens</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Total Tokens</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Costo Est. USD</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.by_model || []).map((m, idx) => (
                    <tr key={`${m.provider}-${m.model}-${idx}`} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <td style={{ padding: "10px 8px", textTransform: "capitalize" }}>{m.provider}</td>
                      <td style={{ padding: "10px 8px", fontWeight: 510, color: "var(--color-paper)" }}><code>{m.model}</code></td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(m.calls)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(m.prompt_tokens)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(m.completion_tokens)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 590, color: "var(--color-paper)" }}>{fmt(m.total_tokens)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", color: "var(--color-pulse-green)", fontWeight: 510 }}>${m.estimated_cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Tab 3: By Ad Account */}
          {activeTab === "accounts" && (
            <div className="glass-card" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "540px" }}>
                <thead>
                  <tr style={{ color: "var(--color-fog)", textAlign: "left", borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "10px 8px" }}>Cuenta Publicitaria</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Llamadas</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Tokens Totales</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Costo Est. USD</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.by_ad_account || []).map((acc, idx) => (
                    <tr key={`${acc.ad_account_id || 'gen'}-${idx}`} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <td style={{ padding: "10px 8px", fontWeight: 510, color: "var(--color-paper)" }}>
                        {acc.ad_account_name}
                      </td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(acc.calls)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 590, color: "var(--color-paper)" }}>{fmt(acc.total_tokens)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", color: "var(--color-pulse-green)", fontWeight: 510 }}>${acc.estimated_cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Tab 4: Timeline */}
          {activeTab === "timeline" && (
            <div className="glass-card" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "500px" }}>
                <thead>
                  <tr style={{ color: "var(--color-fog)", textAlign: "left", borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "10px 8px" }}>Fecha</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Llamadas</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Tokens Consumidos</th>
                    <th style={{ padding: "10px 8px", textAlign: "right" }}>Costo Est. USD</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.daily_timeline || []).map((day) => (
                    <tr key={day.date} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <td style={{ padding: "10px 8px", fontWeight: 510, color: "var(--color-paper)" }}>{day.date}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right" }}>{fmt(day.calls)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 590, color: "var(--color-paper)" }}>{fmt(day.total_tokens)}</td>
                      <td style={{ padding: "10px 8px", textAlign: "right", color: "var(--color-pulse-green)", fontWeight: 510 }}>${day.estimated_cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
