"use client";

import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
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

export default function DashboardPage() {
  const { lastEvent, isConnected } = useWebSocket();
  const [notifications, setNotifications] = useState<string[]>([]);
  
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");

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

  const cplData = campaigns.map(c => ({
    name: c.campaign_name.substring(0, 15) + "...",
    cpl: c.cpl
  })).sort((a, b) => a.cpl - b.cpl).slice(0, 5);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 590, letterSpacing: "-0.012em" }}>Dashboard de Insights</h1>
          <p style={{ color: "var(--color-fog)", marginTop: 8, fontSize: 15 }}>Visión general del rendimiento de tus cuentas publicitarias conectadas.</p>
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
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "right" }}>CPL (S/)</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "right" }}>Inversión</th>
                    <th style={{ padding: "12px 24px", fontWeight: 510, fontSize: 13, color: "var(--color-fog)", textAlign: "center" }}>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.slice(0, 5).map((campaign) => (
                    <tr key={campaign.id} style={{ borderBottom: "1px solid var(--color-graphite)" }}>
                      <td style={{ padding: "12px 24px", fontSize: 14, color: "var(--color-mist)" }}>{campaign.campaign_name}</td>
                      <td style={{ padding: "12px 24px", fontSize: 14, textAlign: "right", color: "var(--color-paper)", fontWeight: 510 }}>{campaign.cpl.toFixed(2)}</td>
                      <td style={{ padding: "12px 24px", fontSize: 14, textAlign: "right", color: "var(--color-fog)" }}>{campaign.spend.toFixed(2)}</td>
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
    </div>
  );
}
