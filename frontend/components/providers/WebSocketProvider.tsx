"use client";

import React, { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "@/lib/api";

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

    let ws: WebSocket | undefined;
    let reconnectTimeout: NodeJS.Timeout;
    let cancelado = false;

    const connect = async () => {
      // Usar variable de entorno en prod, en local asume puerto 8000
      const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

      // La credencial NO va en la URL. La URL de la conexión la registran el
      // proxy y el historial del navegador, y ahí quedaba el access token —
      // 24 horas de vida— en texto plano. Se pide un ticket de un solo uso y
      // 30 segundos, y viaja en `Sec-WebSocket-Protocol`, que es el único
      // campo de cabecera que la API del navegador deja controlar en un
      // handshake y que los logs de acceso no registran.
      let ticket: string;
      try {
        ticket = (await api.getWsTicket()).ticket;
      } catch (e) {
        // Sin ticket no hay canal. Se reintenta como con cualquier corte: la
        // causa habitual es que la sesión todavía no está lista.
        if (!cancelado) reconnectTimeout = setTimeout(connect, 3000);
        return;
      }
      if (cancelado) return;

      ws = new WebSocket(`${wsUrl}/ws/notifications/${tenantId}`, [
        `lejovista.ticket.${ticket}`,
      ]);

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
        // Cada reconexión pide su propio ticket: el anterior ya se consumió.
        if (!cancelado) reconnectTimeout = setTimeout(connect, 3000);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error", err);
        ws?.close();
      };
    };

    connect();

    return () => {
      cancelado = true;
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
