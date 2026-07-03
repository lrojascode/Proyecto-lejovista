"use client";

import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell
} from "recharts";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import { api } from "@/lib/api";
import Link from "next/link";

const COLORS = ['#8B5CF6', '#3B82F6', '#10B981', '#F59E0B', '#EF4444'];

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
      
      if (lastEvent.event_type === "SYNC_STATUS") {
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
    } catch (e) {
      console.error(e);
      setSyncing(false);
      setSyncMessage("Error al iniciar sincronización.");
    }
  };

  if (loading) {
    return (
      <div className="p-6 max-w-7xl mx-auto space-y-8 animate-pulse">
        <div className="h-10 bg-gray-700 rounded w-1/3"></div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          <div className="h-[350px] bg-gray-800 rounded-lg"></div>
          <div className="h-[350px] bg-gray-800 rounded-lg"></div>
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
    <div className="p-6 max-w-7xl mx-auto space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold">Dashboard de Insights</h1>
          <p className="text-gray-400 mt-2">Visión general del rendimiento de tus cuentas publicitarias conectadas.</p>
        </div>
        <div className="flex flex-col items-end space-y-2">
          <div className="flex items-center space-x-2">
            <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`}></div>
            <span className="text-sm text-gray-500">{isConnected ? 'Conectado a Live Updates' : 'Desconectado'}</span>
          </div>
          <button 
            onClick={handleSync}
            disabled={syncing}
            className={`px-4 py-2 rounded-lg font-medium transition-all ${syncing ? 'bg-gray-600 text-gray-300' : 'bg-purple-600 hover:bg-purple-500 text-white shadow-[0_0_15px_rgba(139,92,246,0.3)]'}`}
          >
            {syncing ? '🔄 Sincronizando...' : '🔄 Sincronizar Ahora'}
          </button>
        </div>
      </div>

      {(syncMessage || notifications.length > 0) && (
        <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
          <h3 className="font-semibold text-blue-800 dark:text-blue-300 mb-2">Estado del Sistema</h3>
          {syncMessage && <p className="text-blue-400 mb-2 font-medium">{syncMessage}</p>}
          <ul className="text-sm space-y-1 text-gray-400">
            {notifications.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}

      {!hasData ? (
        <div className="bg-gray-800/50 border border-gray-700 rounded-xl p-12 text-center flex flex-col items-center justify-center space-y-6">
          <div className="w-20 h-20 bg-gray-700 rounded-full flex items-center justify-center">
            <span className="text-4xl">📊</span>
          </div>
          <h2 className="text-2xl font-bold text-white">¡Bienvenido a LejoVista!</h2>
          <p className="text-gray-400 max-w-lg text-lg">
            Parece que tu cuenta publicitaria aún no tiene datos sincronizados, o acabas de conectarla. 
            Haz clic en "Sincronizar Ahora" para extraer las campañas de Meta Ads y generar los primeros diagnósticos con Inteligencia Artificial.
          </p>
          <div className="flex space-x-4 pt-4">
            <button onClick={handleSync} disabled={syncing} className="btn-primary text-lg px-8 py-3 rounded-xl font-bold">
              {syncing ? 'Sincronizando...' : 'Comenzar Sincronización'}
            </button>
            <Link href="/settings" className="px-8 py-3 rounded-xl font-bold border border-gray-600 text-gray-300 hover:bg-gray-800 transition">
              Revisar Configuración
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="bg-white dark:bg-gray-800 p-6 rounded-lg shadow border border-gray-100 dark:border-gray-700">
              <h2 className="text-xl font-semibold mb-4">Top 5 Campañas por Inversión (S/)</h2>
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={spendData} layout="vertical" margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.3} horizontal={true} vertical={false} />
                    <XAxis type="number" stroke="#6B7280" />
                    <YAxis dataKey="name" type="category" stroke="#6B7280" width={100} tick={{fontSize: 12}} />
                    <Tooltip cursor={{fill: 'rgba(255, 255, 255, 0.1)'}} contentStyle={{ backgroundColor: '#1F2937', borderColor: '#374151', color: '#fff' }} />
                    <Bar dataKey="spend" name="Inversión (S/)" fill="#8B5CF6" radius={[0, 4, 4, 0]}>
                      {spendData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-white dark:bg-gray-800 p-6 rounded-lg shadow border border-gray-100 dark:border-gray-700">
              <h2 className="text-xl font-semibold mb-4">Top 5 Campañas por Menor CPL (S/)</h2>
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={cplData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.3} />
                    <XAxis dataKey="name" stroke="#6B7280" tick={{fontSize: 12}} />
                    <YAxis stroke="#6B7280" />
                    <Tooltip cursor={{fill: 'rgba(255, 255, 255, 0.1)'}} contentStyle={{ backgroundColor: '#1F2937', borderColor: '#374151', color: '#fff' }} />
                    <Bar dataKey="cpl" name="CPL (S/)" fill="#10B981" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-lg shadow border border-gray-100 dark:border-gray-700 overflow-hidden">
            <div className="p-6 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center">
              <h2 className="text-xl font-semibold">Resumen de Campañas Activas</h2>
              <Link href="/campaigns" className="text-purple-400 hover:text-purple-300 text-sm font-medium">Ver todas →</Link>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700">
                    <th className="p-4 font-semibold text-gray-600 dark:text-gray-300">Campaña</th>
                    <th className="p-4 font-semibold text-gray-600 dark:text-gray-300 text-right">CPL (S/)</th>
                    <th className="p-4 font-semibold text-gray-600 dark:text-gray-300 text-right">Inversión</th>
                    <th className="p-4 font-semibold text-gray-600 dark:text-gray-300 text-center">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.slice(0, 5).map((campaign) => (
                    <tr key={campaign.id} className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/50">
                      <td className="p-4 font-medium">{campaign.campaign_name}</td>
                      <td className="p-4 text-right text-blue-600 dark:text-blue-400 font-bold">{campaign.cpl.toFixed(2)}</td>
                      <td className="p-4 text-right text-gray-600 dark:text-gray-400">{campaign.spend.toFixed(2)}</td>
                      <td className="p-4 text-center">
                        <span className={`px-2 py-1 text-xs font-semibold rounded-full ${
                          campaign.status === 'ACTIVE' 
                            ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' 
                            : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
                        }`}>
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
