"use client";

import React, { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { getAccessToken } from "@/lib/api";

// Este contexto exporta el último evento recibido para que los componentes reaccionen
type WSEvent = {
  event_type: string;
  job_id: string;
  status: string;
  tenant_id: string;
  [key: string]: any;
};

interface WebSocketContextType {
  lastEvent: WSEvent | null;
  isConnected: boolean;
}

const WebSocketContext = createContext<WebSocketContextType>({ lastEvent: null, isConnected: false });

export const useWebSocket = () => useContext(WebSocketContext);

export const WebSocketProvider = ({ children, tenantId }: { children: ReactNode; tenantId: string }) => {
  const [lastEvent, setLastEvent] = useState<WSEvent | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    // No intentar conectar sin un tenant (organization_id) válido
    if (!tenantId) {
      setIsConnected(false);
      return;
    }

    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connect = () => {
      // Usar variable de entorno en prod, en local asume puerto 8000
      const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
      // El backend exige el JWT por query param y valida la pertenencia al
      // tenant. (S-2) El access token vive en memoria (lib/api.ts) — el
      // layout ya completó bootstrapSession() antes de montar este provider.
      const token = getAccessToken();
      const qs = token ? `?token=${encodeURIComponent(token)}` : "";
      ws = new WebSocket(`${wsUrl}/ws/notifications/${tenantId}${qs}`);

      ws.onopen = () => {
        console.log("WebSocket connected");
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          console.log("WS received:", data);
          setLastEvent(data);
          
          // Toast notification here if desired
          // toast.success(`Status updated to ${data.status}`)
        } catch (e) {
          console.error("Failed to parse WS message", e);
        }
      };

      ws.onclose = () => {
        console.log("WebSocket disconnected. Reconnecting in 3s...");
        setIsConnected(false);
        reconnectTimeout = setTimeout(connect, 3000);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error", err);
        ws.close();
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, [tenantId]);

  return (
    <WebSocketContext.Provider value={{ lastEvent, isConnected }}>
      {children}
    </WebSocketContext.Provider>
  );
};
