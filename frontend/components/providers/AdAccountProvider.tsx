"use client";

import React, { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "@/lib/api";

export type AdAccountSummary = {
  id: string;
  name: string;
  external_account_id: string;
  status: string;
  // Página de Facebook predeterminada para campañas (Settings → Páginas y
  // WhatsApp); el formulario de creación la preselecciona.
  default_page_id?: string | null;
};

interface AdAccountContextType {
  accounts: AdAccountSummary[];
  activeAdAccountId: string | null;
  setActiveAdAccountId: (id: string) => void;
  loading: boolean;
}

const AdAccountContext = createContext<AdAccountContextType>({
  accounts: [],
  activeAdAccountId: null,
  setActiveAdAccountId: () => {},
  loading: true,
});

export const useAdAccount = () => useContext(AdAccountContext);

// Clave namespaced por organización: persiste la selección entre sesiones sin
// que cambiar de tenant/subdominio arrastre la cuenta activa de otra org.
const namespacedKey = (orgId: string) => `active_ad_account_${orgId}`;
// Clave fija: la que `apiRequest` (lib/api.ts) lee de forma síncrona para armar
// el header X-Ad-Account-Id — este Provider es su único escritor.
const CURRENT_KEY = "active_ad_account_id";

export const AdAccountProvider = ({ children, orgId }: { children: ReactNode; orgId: string }) => {
  const [accounts, setAccounts] = useState<AdAccountSummary[]>([]);
  const [activeAdAccountId, setActiveAdAccountIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const setActiveAdAccountId = (id: string) => {
    setActiveAdAccountIdState(id);
    if (orgId) localStorage.setItem(namespacedKey(orgId), id);
    localStorage.setItem(CURRENT_KEY, id);
  };

  useEffect(() => {
    if (!orgId) {
      setLoading(false);
      return;
    }

    let cancelled = false;

    const load = async () => {
      setLoading(true);
      try {
        const data: AdAccountSummary[] = await api.getAdAccounts();
        if (cancelled) return;
        setAccounts(data);

        const saved = localStorage.getItem(namespacedKey(orgId));
        let resolved: string | null = null;
        if (saved && data.some((a) => a.id === saved)) {
          resolved = saved;
        } else if (data.length === 1) {
          resolved = data[0].id;
        }

        setActiveAdAccountIdState(resolved);
        if (resolved) {
          localStorage.setItem(namespacedKey(orgId), resolved);
          localStorage.setItem(CURRENT_KEY, resolved);
        } else {
          localStorage.removeItem(CURRENT_KEY);
        }
      } catch (e) {
        console.error("Error fetching ad accounts:", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  return (
    <AdAccountContext.Provider value={{ accounts, activeAdAccountId, setActiveAdAccountId, loading }}>
      {children}
    </AdAccountContext.Provider>
  );
};
