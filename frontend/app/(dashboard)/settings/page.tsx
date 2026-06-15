"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Settings() {
  const [org, setOrg] = useState<any>(null);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Settings Form States
  const [aiModel, setAiModel] = useState("google/gemini-2.5-flash");
  const [maxBudget, setMaxBudget] = useState(1000);
  
  // Connection Form States
  const [accName, setAccName] = useState("");
  const [extId, setExtId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  
  const [msg, setMsg] = useState("");
  const [connMsg, setConnMsg] = useState("");

  const fetchData = async () => {
    try {
      const orgData = await api.getOrg();
      const accountsData = await api.getAdAccounts();
      setOrg(orgData);
      setAccounts(accountsData);
      
      setAiModel(orgData.ai_model);
      setMaxBudget(orgData.max_budget);
    } catch (e) {
      console.error("Error fetching settings:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.updateOrg({ ai_model: aiModel, max_budget: maxBudget });
      setMsg("✓ Configuración de organización guardada con éxito.");
    } catch (err: any) {
      setMsg(`❌ Error: ${err.message}`);
    }
  };

  const handleConnectAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    setConnMsg("");
    try {
      await api.connectAdAccount({
        platform: "META",
        name: accName,
        external_account_id: extId,
        access_token: accessToken,
      });
      setConnMsg("✓ Cuenta conectada. Sincronización en curso en segundo plano.");
      setAccName("");
      setExtId("");
      setAccessToken("");
      fetchData();
    } catch (err: any) {
      setConnMsg(`❌ Error: ${err.message}`);
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando configuración...</div>;
  }

  return (
    <div>
      <div style={{ marginBottom: "2rem" }}>
        <h2 style={{ fontSize: "2rem", fontWeight: "700" }}>Configuración del Sistema</h2>
        <p style={{ color: "var(--text-secondary)" }}>Configura tus credenciales, cuentas publicitarias de Meta y límites financieros.</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1.8fr", gap: "2rem" }}>
        {/* Org Settings */}
        <div className="glass-card" style={{ height: "fit-content" }}>
          <h3 style={{ fontSize: "1.25rem", marginBottom: "1.25rem" }}>🏢 Preferencias Generales</h3>
          
          {msg && (
            <div style={{
              background: "rgba(16, 185, 129, 0.1)",
              border: "1px solid rgba(16, 185, 129, 0.2)",
              color: "var(--success)",
              padding: "0.5rem 0.75rem",
              borderRadius: "6px",
              fontSize: "0.85rem",
              marginBottom: "1rem"
            }}>
              {msg}
            </div>
          )}

          <form onSubmit={handleSaveSettings} style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <div>
              <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                Modelo de IA Preferido
              </label>
              <select 
                className="form-input" 
                value={aiModel} 
                onChange={(e) => setAiModel(e.target.value)}
                style={{ background: "#171821" }}
              >
                <option value="google/gemini-2.5-flash">Gemini 2.5 Flash (Estándar)</option>
                <option value="google/gemini-2.5-pro">Gemini 2.5 Pro (Avanzado)</option>
                <option value="anthropic/claude-3-5-sonnet">Claude 3.5 Sonnet (Premium)</option>
              </select>
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                Límite de Presupuesto Mensual de Seguridad (S/.)
              </label>
              <input
                type="number"
                className="form-input"
                required
                value={maxBudget}
                onChange={(e) => setMaxBudget(Number(e.target.value))}
              />
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem", display: "block" }}>
                La IA detendrá cualquier creación o escalado si excede este monto.
              </span>
            </div>

            <button type="submit" className="btn-primary" style={{ alignSelf: "flex-start" }}>
              Guardar Cambios
            </button>
          </form>
        </div>

        {/* Ad Accounts Connections */}
        <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
          
          {/* List connected */}
          <div className="glass-card">
            <h3 style={{ fontSize: "1.25rem", marginBottom: "1rem" }}>🔗 Cuentas Publicitarias Conectadas</h3>
            {accounts.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No tienes ninguna cuenta conectada.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {accounts.map((acc) => (
                  <div 
                    key={acc.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "1rem",
                      borderRadius: "10px",
                      background: "rgba(255,255,255,0.02)",
                      border: "1px solid var(--border-color)"
                    }}
                  >
                    <div>
                      <strong style={{ color: "#fff" }}>{acc.name}</strong>
                      <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                        Meta Ads ID: <code>{acc.external_account_id}</code>
                      </p>
                    </div>
                    <span className="badge badge-success">Activo</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Connect new */}
          <div className="glass-card">
            <h3 style={{ fontSize: "1.25rem", marginBottom: "1.25rem" }}>➕ Conectar Cuenta de Meta Ads</h3>
            
            {connMsg && (
              <div style={{
                background: connMsg.includes("Error") ? "rgba(239, 68, 68, 0.1)" : "rgba(16, 185, 129, 0.1)",
                border: `1px solid ${connMsg.includes("Error") ? "rgba(239, 68, 68, 0.2)" : "rgba(16, 185, 129, 0.2)"}`,
                color: connMsg.includes("Error") ? "var(--danger)" : "var(--success)",
                padding: "0.5rem 0.75rem",
                borderRadius: "6px",
                fontSize: "0.85rem",
                marginBottom: "1rem"
              }}>
                {connMsg}
              </div>
            )}

            <form onSubmit={handleConnectAccount} style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
                <div>
                  <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                    Nombre Descriptivo
                  </label>
                  <input
                    type="text"
                    className="form-input"
                    required
                    value={accName}
                    onChange={(e) => setAccName(e.target.value)}
                    placeholder="Ej: MTV Cables Principal"
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                    ID de Cuenta (act_XXX)
                  </label>
                  <input
                    type="text"
                    className="form-input"
                    required
                    value={extId}
                    onChange={(e) => setExtId(e.target.value)}
                    placeholder="123456789"
                  />
                </div>
              </div>

              <div>
                <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Access Token (Meta Ads API)
                </label>
                <input
                  type="password"
                  className="form-input"
                  required
                  value={accessToken}
                  onChange={(e) => setAccessToken(e.target.value)}
                  placeholder="EAAB..."
                />
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem", display: "block" }}>
                  Tus tokens de acceso se almacenan cifrados en nuestra base de datos con AES-256.
                </span>
              </div>

              <button type="submit" className="btn-primary" style={{ alignSelf: "flex-end" }}>
                Conectar Cuenta
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
