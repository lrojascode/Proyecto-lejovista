"use client";

import { useEffect, useState } from "react";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell
} from "recharts";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import { api } from "@/lib/api";
import Link from "next/link";

// Acentos de apoyo del design system — nunca acid-lime (reservado para acciones)
const COLORS = ['#6366f1', '#02b8cc', '#27a644', '#8b5cf6', '#f2994a'];

const chartTooltipStyle = {
  backgroundColor: '#161718',
  border: '1px solid #23252a',
  borderRadius: 6,
  color: '#d0d6e0',
  fontSize: 13,
};

// 5.1 (E-3): flecha + color de un Δ%. `goodWhen` indica qué dirección es
// una buena noticia para esa métrica (costo/conv: bajar es bueno; conversa-
// ciones: subir es bueno; gasto: no hay "bueno/malo" objetivo, es neutral).
function DeltaBadge({ pct, goodWhen }: { pct: number | null | undefined; goodWhen: "up" | "down" | "neutral" }) {
  if (pct === null || pct === undefined) return null;
  const isUp = pct > 0;
  const isFlat = pct === 0;
  let color = "var(--color-fog)";
  if (!isFlat && goodWhen !== "neutral") {
    const isGood = (goodWhen === "up") === isUp;
    color = isGood ? "var(--color-pulse-green)" : "var(--color-coral-red)";
  }
  const arrow = isFlat ? "→" : isUp ? "↑" : "↓";
  return (
    <span style={{ color, fontSize: 13, fontWeight: 510, marginLeft: 8 }}>
      {arrow} {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

function timeAgo(iso: string | null): string {
  if (!iso) return "nunca";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "hace instantes";
  if (mins < 60) return `hace ${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  return `hace ${days} d`;
}

const ALERT_LEVEL_ORDER: Record<string, number> = { CRITICAL: 0, WARNING: 1, INFO: 2 };

export default function DashboardPage() {
  const { lastEvent, isConnected } = useWebSocket();
  const [notifications, setNotifications] = useState<string[]>([]);

  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [apiCallHistory, setApiCallHistory] = useState<any[]>([]);
  const [spendHistory, setSpendHistory] = useState<any[]>([]);
  const [businessPolicies, setBusinessPolicies] = useState<{ industry: string | null; policies: { title: string; description: string }[] } | null>(null);
  const [kpisSummary, setKpisSummary] = useState<any | null>(null);
  const [topAlerts, setTopAlerts] = useState<any[]>([]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await api.getRecentCampaigns();
      setCampaigns(data || []);
    } catch (e) {
      console.error("Error fetching dashboard data:", e);
    } finally {
      setLoading(false);
    }
    try {
      const history = await api.getApiCallHistory(30);
      setApiCallHistory(history || []);
    } catch (e) {
      console.error("Error fetching API call history:", e);
    }
    try {
      const spend = await api.getSpendHistory();
      setSpendHistory(spend?.months || []);
    } catch (e) {
      console.error("Error fetching spend history:", e);
    }
    try {
      const policies = await api.getBusinessPolicies();
      setBusinessPolicies(policies);
    } catch (e) {
      console.error("Error fetching business policies:", e);
    }
    try {
      const kpis = await api.getKpisSummary();
      setKpisSummary(kpis);
    } catch (e) {
      console.error("Error fetching KPIs summary:", e);
    }
    try {
      const insights = await api.getInsights();
      const sorted = (insights || []).slice().sort(
        (a: any, b: any) => (ALERT_LEVEL_ORDER[a.level] ?? 9) - (ALERT_LEVEL_ORDER[b.level] ?? 9)
      );
      setTopAlerts(sorted.slice(0, 3));
    } catch (e) {
      console.error("Error fetching insights:", e);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    if (lastEvent) {
      const msg = `[${new Date().toLocaleTimeString()}] ${lastEvent.event_type}: ${lastEvent.status}`;
      setNotifications(prev => [...prev, msg].slice(-3));
      
      if (lastEvent.event_type === "sync_status") {
        if (lastEvent.status === "COMPLETED") {
          setSyncing(false);
          setSyncMessage("Sincronización completada exitosamente.");
          fetchData(); // Reload data
        } else if (lastEvent.status === "FAILED") {
          setSyncing(false);
          setSyncMessage("Error en la sincronización.");
        } else {
          setSyncing(true);
          setSyncMessage(`Sincronizando: ${lastEvent.status}`);
        }
      }
    }
  }, [lastEvent]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage("Iniciando sincronización...");
    try {
      await api.triggerSync();
    } catch (e: any) {
      console.error(e);
      setSyncing(false);
      // Mostrar el motivo real (p. ej. cooldown "espera 60s" o "no hay cuentas activas").
      setSyncMessage(e?.message || "Error al iniciar sincronización.");
    }
  };

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 24, animation: "pulse 1.5s infinite" }}>
        <div style={{ height: 40, width: "33%", background: "var(--color-obsidian)", borderRadius: 6 }}></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 24 }}>
          <div style={{ height: 350, background: "var(--color-carbon)", borderRadius: 12 }}></div>
          <div style={{ height: 350, background: "var(--color-carbon)", borderRadius: 12 }}></div>
        </div>
      </div>
    );
  }

  const hasData = campaigns.length > 0;
  
  // Procesar datos para gráficos
  const spendData = campaigns.map(c => ({
    name: c.campaign_name.substring(0, 15) + "...",
    spend: c.spend
  })).sort((a, b) => b.spend - a.spend).slice(0, 5);

  // E-4: ranking por costo/conversación de los últimos 7 días (agregado real,
  // no el snapshot de un solo día que puede tener 0 gasto o un pico atípico).
  // Se excluyen campañas sin conversiones en la ventana — dividirían por 0 y
  // ensuciarían el "menor CPL" con falsos ganadores de gasto cero.
  const cplData = campaigns
    .filter(c => (c.conversations_7d ?? c.conversions) > 0)
    .map(c => ({
      name: c.campaign_name.substring(0, 15) + "...",
      cpl: c.cost_per_conv_7d ?? c.cpl
    }))
    .sort((a, b) => a.cpl - b.cpl)
    .slice(0, 5);

  const MONTH_LABELS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  const monthlySpendData = spendHistory.map((m) => {
    const [year, month] = m.year_month.split("-");
    return { name: `${MONTH_LABELS[parseInt(month, 10) - 1]} ${year.slice(2)}`, spend: m.spend };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 590, letterSpacing: "-0.012em" }}>Dashboard de Insights</h1>
          <p style={{ color: "var(--color-fog)", marginTop: 8, fontSize: 15 }}>Visión general del rendimiento de tus cuentas publicitarias conectadas.</p>
          {kpisSummary && (
            <p style={{ color: "var(--color-fog)", marginTop: 4, fontSize: 13 }}>
              Última sincronización: {timeAgo(kpisSummary.last_sync_at)}
            </p>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 8, height: 8, borderRadius: 9999,
              background: isConnected ? "var(--color-pulse-green)" : "var(--color-coral-red)"
            }}></div>
            <span style={{ fontSize: 13, color: "var(--color-fog)" }}>{isConnected ? 'Conectado a Live Updates' : 'Desconectado'}</span>
          </div>
          {hasData && (
            <button onClick={handleSync} disabled={syncing} className="btn-primary">
              {syncing ? 'Sincronizando...' : 'Sincronizar Ahora'}
            </button>
          )}
        </div>
      </div>

      {(syncMessage || notifications.length > 0) && (
        <div className="card-subtle">
          <h3 style={{ fontSize: 13, fontWeight: 510, color: "var(--color-mist)", marginBottom: 8 }}>Estado del Sistema</h3>
          {syncMessage && <p style={{ color: "var(--color-mist)", marginBottom: 8, fontSize: 14 }}>{syncMessage}</p>}
          <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
            {notifications.map((n, i) => <li key={i} className="mono" style={{ color: "var(--color-fog)" }}>{n}</li>)}
          </ul>
        </div>
      )}

      {hasData && kpisSummary && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          <div className="glass-card" style={{ padding: 20 }}>
            <p style={{ color: "var(--color-fog)", fontSize: 13, marginBottom: 6 }}>Gasto (7d)</p>
            <p style={{ fontSize: 22, fontWeight: 590 }}>
              S/. {kpisSummary.kpis_7d.spend.toFixed(2)}
              <DeltaBadge pct={kpisSummary.deltas_pct.spend} goodWhen="neutral" />
            </p>
          </div>
          <div className="glass-card" style={{ padding: 20 }}>
            <p style={{ color: "var(--color-fog)", fontSize: 13, marginBottom: 6 }}>Conversaciones (7d)</p>
            <p style={{ fontSize: 22, fontWeight: 590 }}>
              {kpisSummary.kpis_7d.conversations}
              <DeltaBadge pct={kpisSummary.deltas_pct.conversations} goodWhen="up" />
            </p>
          </div>
          <div className="glass-card" style={{ padding: 20 }}>
            <p style={{ color: "var(--color-fog)", fontSize: 13, marginBottom: 6 }}>Costo/Conv. (7d)</p>
            <p style={{ fontSize: 22, fontWeight: 590 }}>
              S/. {kpisSummary.kpis_7d.cost_per_conv.toFixed(2)}
              <DeltaBadge pct={kpisSummary.deltas_pct.cost_per_conv} goodWhen="down" />
            </p>
          </div>
          <Link href="/diagnostics" className="glass-card" style={{
            padding: 20, textDecoration: "none", color: "inherit", display: "block",
          }}>
            <p style={{ color: "var(--color-fog)", fontSize: 13, marginBottom: 6 }}>Alertas Críticas</p>
            <p style={{
              fontSize: 22, fontWeight: 590,
              color: kpisSummary.critical_alerts_count > 0 ? "var(--color-coral-red)" : "var(--color-paper)",
            }}>
              {kpisSummary.critical_alerts_count}
            </p>
          </Link>
        </div>
      )}

      {hasData && topAlerts.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
          {topAlerts.map((a) => (
            <Link key={a.id} href="/diagnostics" className="glass-card" style={{
              padding: 16, textDecoration: "none", color: "inherit", display: "block",
              borderLeft: `3px solid ${a.level === "CRITICAL" ? "var(--color-coral-red)" : a.level === "WARNING" ? "var(--color-signal-teal)" : "var(--color-graphite)"}`,
            }}>
              <span className={`badge ${a.level === "CRITICAL" ? "badge-danger" : a.level === "WARNING" ? "badge-warning" : "badge-success"}`} style={{ marginBottom: 8, display: "inline-block" }}>
                {a.level}
              </span>
              <p style={{ fontSize: 14, fontWeight: 510, marginBottom: 4 }}>{a.title}</p>
              <p style={{ fontSize: 13, color: "var(--color-fog)" }}>{a.description.slice(0, 90)}{a.description.length > 90 ? "…" : ""}</p>
            </Link>
          ))}
        </div>
      )}

      {!hasData ? (
        <div className="glass-card" style={{
          padding: 48, textAlign: "center", display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 16
        }}>
          <h2 style={{ fontSize: 24, fontWeight: 590, letterSpacing: "-0.012em" }}>Bienvenido a LejoVista</h2>
          <p style={{ color: "var(--color-fog)", maxWidth: 520, fontSize: 15, lineHeight: 1.6 }}>
            Parece que tu cuenta publicitaria aún no tiene datos sincronizados, o acabas de conectarla.
            Inicia la sincronización para extraer las campañas de Meta Ads y generar los primeros diagnósticos con Inteligencia Artificial.
          </p>
          <div style={{ display: "flex", gap: 12, paddingTop: 8 }}>
            <button onClick={handleSync} disabled={syncing} className="btn-primary">
              {syncing ? 'Sincronizando...' : 'Comenzar Sincronización'}
            </button>
            <Link href="/settings" className="btn-secondary">
              Revisar Configuración
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 24 }}>
            <div className="glass-card">
              <h2 style={{ fontSize: 16, fontWeight: 510, marginBottom: 16 }}>Top 5 Campañas por Inversión (S/)</h2>
              <div style={{ height: 300, width: "100%" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={spendData} layout="vertical" margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#23252a" horizontal={true} vertical={false} />
                    <XAxis type="number" stroke="#62666d" tick={{ fontSize: 12 }} />
                    <YAxis dataKey="name" type="category" stroke="#62666d" width={100} tick={{fontSize: 12}} />
                    <Tooltip cursor={{fill: 'rgba(255, 255, 255, 0.04)'}} contentStyle={chartTooltipStyle} />
                    <Bar dataKey="spend" name="Inversión (S/)" fill="#6366f1" radius={[0, 4, 4, 0]}>
                      {spendData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="glass-card">
              <h2 style={{ fontSize: 16, fontWeight: 510, marginBottom: 16 }}>Top 5 Campañas por Menor CPL (S/)</h2>
              <div style={{ height: 300, width: "100%" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={cplData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#23252a" />
                    <XAxis dataKey="name" stroke="#62666d" tick={{fontSize: 12}} />
                    <YAxis stroke="#62666d" tick={{ fontSize: 12 }} />
                    <Tooltip cursor={{fill: 'rgba(255, 255, 255, 0.04)'}} contentStyle={chartTooltipStyle} />
                    <Bar dataKey="cpl" name="CPL (S/)" fill="#02b8cc" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {monthlySpendData.length > 0 && (
            <div className="glass-card">
              <h2 style={{ fontSize: 16, fontWeight: 510, marginBottom: 4 }}>Histórico de Inversión Mensual</h2>
              <p style={{ color: "var(--color-fog)", fontSize: 13, marginBottom: 16 }}>
                Gasto total por mes en esta cuenta (sin desglose por campaña).
              </p>
              <div style={{ height: 260, width: "100%" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={monthlySpendData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#23252a" />
                    <XAxis dataKey="name" stroke="#62666d" tick={{ fontSize: 12 }} />
                    <YAxis stroke="#62666d" tick={{ fontSize: 12 }} />
                    <Tooltip cursor={{ stroke: 'rgba(255, 255, 255, 0.15)' }} contentStyle={chartTooltipStyle} />
                    <Line type="monotone" dataKey="spend" name="Inversión (S/)" stroke="#6366f1" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          <div className="glass-card" style={{ padding: 0, overflow: "hidden" }}>
            <div style={{
              padding: "16px 24px", borderBottom: "1px solid var(--color-graphite)",
              display: "flex", justifyContent: "space-between", alignItems: "center"
            }}>
              <h2 style={{ fontSize: 16, fontWeight: 510 }}>Resumen de Campañas Activas</h2>
              <Link href="/campaigns" style={{ color: "var(--color-fog)", fontSize: 13 }}>Ver todas →</Link>
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", textAlign: "left", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)" }}>Campaña</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "right" }}>CPL 7d (S/)</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "right" }}>Inversión</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "center" }}>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.slice(0, 5).map((campaign) => (
                    <tr key={campaign.id} style={{ borderBottom: "1px solid var(--color-graphite)" }}>
                      <td style={{ padding: "12px 24px", fontSize: 14, color: "var(--color-mist)" }}>{campaign.campaign_name}</td>
                      <td style={{ padding: "12px 24px", fontSize: 14, textAlign: "right", color: "var(--color-paper)", fontWeight: 510 }}>{(campaign.cost_per_conv_7d ?? campaign.cpl).toFixed(2)}</td>
                      <td style={{ padding: "12px 24px", fontSize: 14, textAlign: "right", color: "var(--color-fog)" }}>{(campaign.spend_7d ?? campaign.spend).toFixed(2)}</td>
                      <td style={{ padding: "12px 24px", textAlign: "center" }}>
                        <span className={`badge ${campaign.status === 'ACTIVE' ? 'badge-success' : 'badge-warning'}`}>
                          {campaign.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {businessPolicies && (
        <div className="glass-card">
          <h2 style={{ fontSize: 16, fontWeight: 510, marginBottom: 4 }}>Políticas de Meta relevantes para tu negocio</h2>
          <p style={{ color: "var(--color-fog)", fontSize: 13, marginBottom: 16 }}>
            Recordatorios generales, no un sustituto de la política oficial de Meta.
            {!businessPolicies.industry && (
              <> <Link href="/settings" style={{ color: "var(--color-signal-teal)" }}>Selecciona tu rubro en Configuración</Link> para ver recordatorios más específicos.</>
            )}
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {businessPolicies.policies.map((p, i) => (
              <div key={i} style={{ borderLeft: "2px solid var(--color-signal-teal)", paddingLeft: 12 }}>
                <p style={{ fontWeight: 510, fontSize: 14, marginBottom: 2 }}>{p.title}</p>
                <p style={{ color: "var(--color-fog)", fontSize: 13 }}>{p.description}</p>
              </div>
            ))}
          </div>
          <a
            href="https://www.facebook.com/policies/ads/"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--color-fog)", fontSize: 12, marginTop: 16, display: "inline-block" }}
          >
            Ver la política oficial completa de Meta →
          </a>
        </div>
      )}

      {/* 5.1 (E-3): antes era una tabla siempre abierta compitiendo por atención
          con las métricas de negocio — ahora vive colapsada como "Actividad
          técnica", un detalle de auditoría, no el foco del dashboard. */}
      <details className="glass-card" style={{ padding: 0, overflow: "hidden" }}>
        <summary style={{
          padding: "16px 24px", cursor: "pointer", listStyle: "none",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 510, display: "inline" }}>Actividad técnica</h2>
            <p style={{ color: "var(--color-fog)", fontSize: 13, marginTop: 4 }}>Historial de llamadas a la API de Meta — últimas {apiCallHistory.length} solicitudes, para auditar cuándo y qué se sincronizó.</p>
          </div>
        </summary>
        <div style={{ borderTop: "1px solid var(--color-graphite)" }}>
          {apiCallHistory.length === 0 ? (
            <p style={{ padding: 24, color: "var(--color-fog)", fontSize: 14 }}>Todavía no hay llamadas registradas.</p>
          ) : (
            <div style={{ overflowX: "auto", overflowY: "auto", maxHeight: 460 }}>
              <table style={{ width: "100%", textAlign: "left", borderCollapse: "collapse" }}>
                <thead style={{ position: "sticky", top: 0, background: "var(--color-carbon)", zIndex: 1 }}>
                  <tr style={{ borderBottom: "1px solid var(--color-graphite)" }}>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)" }}>Fecha/Hora</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)" }}>Cuenta</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)" }}>Método</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)" }}>Endpoint</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "center" }}>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {apiCallHistory.map((call) => (
                    <tr key={call.id} style={{ borderBottom: "1px solid var(--color-graphite)" }}>
                      <td className="mono" style={{ padding: "12px 24px", fontSize: 13, color: "var(--color-fog)" }}>
                        {new Date(call.timestamp).toLocaleString()}
                      </td>
                      <td style={{ padding: "12px 24px", fontSize: 14, color: "var(--color-mist)" }}>{call.ad_account_name}</td>
                      <td className="mono" style={{ padding: "12px 24px", fontSize: 13, color: "var(--color-fog)" }}>{call.method}</td>
                      <td className="mono" style={{ padding: "12px 24px", fontSize: 13, color: "var(--color-mist)" }}>{call.endpoint}</td>
                      <td style={{ padding: "12px 24px", textAlign: "center" }}>
                        <span className={`badge ${call.status_code >= 200 && call.status_code < 300 ? 'badge-success' : 'badge-danger'}`}>
                          {call.status_code}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
