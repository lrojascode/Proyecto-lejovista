"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAdAccount } from "@/components/providers/AdAccountProvider";
import { badgeIcon } from "@/lib/badge";
import type { Organization, AdAccount, MetaPage, WhatsAppNumber } from "@/lib/types";

export default function Settings() {
  const { activeAdAccountId, setActiveAdAccountId } = useAdAccount();
  const [org, setOrg] = useState<Organization | null>(null);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [loading, setLoading] = useState(true);

  // Settings Form States
  const [aiModel, setAiModel] = useState("google/gemini-3.5-flash");
  const [maxBudget, setMaxBudget] = useState(1000);
  const [aiModels, setAiModels] = useState<{ value: string; label: string }[]>([]);
  const [aiProvider, setAiProvider] = useState<string>("");
  const [industry, setIndustry] = useState("");
  const [industries, setIndustries] = useState<{ value: string; label: string }[]>([]);

  // LLM Custom Key States
  const [llmProvider, setLlmProvider] = useState<string>("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmKeyTestMsg, setLlmKeyTestMsg] = useState<{ valid: boolean; text: string } | null>(null);
  const [testingLlmKey, setTestingLlmKey] = useState(false);
  
  // Connection Form States
  const [accName, setAccName] = useState("");
  const [extId, setExtId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  
  const [msg, setMsg] = useState("");
  const [connMsg, setConnMsg] = useState("");
  const [archiveMsg, setArchiveMsg] = useState("");
  const [confirmArchiveId, setConfirmArchiveId] = useState<string | null>(null);

  // Páginas de Facebook + WhatsApp
  const [pages, setPages] = useState<MetaPage[]>([]);
  const [pagesMsg, setPagesMsg] = useState("");
  const [refreshingPages, setRefreshingPages] = useState(false);
  const [waInputs, setWaInputs] = useState<Record<string, string>>({});
  const [waSavingId, setWaSavingId] = useState<string | null>(null);
  const [defaultSavingId, setDefaultSavingId] = useState<string | null>(null);

  // Catálogo de números de WhatsApp de la organización (F1)
  const [numbers, setNumbers] = useState<WhatsAppNumber[]>([]);
  const [newNumber, setNewNumber] = useState("");
  const [numberEdits, setNumberEdits] = useState<Record<string, string>>({});
  const [numberSavingId, setNumberSavingId] = useState<string | null>(null);

  // Cuentas archivadas (0.4)
  const [archivedAccounts, setArchivedAccounts] = useState<AdAccount[]>([]);
  const [showArchived, setShowArchived] = useState(false);

  // Reconexión simplificada (4.4)
  const [reconnectingAccount, setReconnectingAccount] = useState<AdAccount | null>(null);
  const [reconnectToken, setReconnectToken] = useState("");
  const [reconnectMsg, setReconnectMsg] = useState("");
  const [reconnectLoading, setReconnectLoading] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deleteInputName, setDeleteInputName] = useState("");

  const fetchData = async () => {
    try {
      const orgData = await api.getOrg();
      const allAccounts = await api.getAdAccounts(true);
      const activeList = allAccounts.filter((a) => a.status !== "ARCHIVED");
      const archivedList = allAccounts.filter((a) => a.status === "ARCHIVED");
      setOrg(orgData);
      setAccounts(activeList);
      setArchivedAccounts(archivedList);
      setMaxBudget(orgData.max_budget);
      setIndustry(orgData.industry || "");
      setLlmProvider(orgData.llm_provider || "");
      setLlmApiKey("");
      setLlmKeyTestMsg(null);

      const pagesData = await api.getMyPages();
      setPages(pagesData);

      setNumbers(await api.getWhatsAppNumbers());

      const policiesData = await api.getBusinessPolicies();
      setIndustries(policiesData.available_industries || []);

      // Solo se ofrecen los modelos del proveedor de IA realmente activo
      // (según qué API key esté configurada en el backend), para no guardar
      // un modelo que el proveedor activo no reconocería.
      const modelsData = await api.getAiModels(orgData.llm_provider || undefined);
      setAiProvider(modelsData.active_provider);
      setAiModels(modelsData.models);
      const isKnownModel = modelsData.models.some((m) => m.value === orgData.ai_model);
      setAiModel(isKnownModel ? orgData.ai_model : modelsData.models[0]?.value ?? orgData.ai_model);
    } catch (e) {
      console.error("Error fetching settings:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleProviderChange = async (newProvider: string) => {
    setLlmProvider(newProvider);
    setLlmKeyTestMsg(null);
    try {
      const modelsData = await api.getAiModels(newProvider || undefined);
      setAiModels(modelsData.models);
      if (modelsData.models.length > 0 && !modelsData.models.some((m) => m.value === aiModel)) {
        setAiModel(modelsData.models[0].value);
      }
    } catch (err) {
      console.error("Error loading models for provider:", err);
    }
  };

  const handleTestLlmKey = async () => {
    if (!llmApiKey && !org?.has_llm_api_key) {
      setLlmKeyTestMsg({ valid: false, text: "Ingresa una API Key para probarla." });
      return;
    }
    if (!llmProvider) {
      setLlmKeyTestMsg({ valid: false, text: "Selecciona un proveedor de IA." });
      return;
    }
    setTestingLlmKey(true);
    setLlmKeyTestMsg(null);
    try {
      const res = await api.testLLMKey({
        provider: llmProvider,
        api_key: llmApiKey,
        model: aiModel,
      });
      setLlmKeyTestMsg({ valid: res.valid, text: res.message });
    } catch (err: any) {
      setLlmKeyTestMsg({ valid: false, text: err.message || "Error al probar la credencial." });
    } finally {
      setTestingLlmKey(false);
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg("");
    try {
      const payload: any = {
        ai_model: aiModel,
        max_budget: maxBudget,
        industry: industry || null,
        llm_provider: llmProvider || null,
      };
      if (llmApiKey.trim()) {
        payload.llm_api_key = llmApiKey.trim();
      } else if (llmProvider === "" && org?.has_llm_api_key) {
        payload.llm_api_key = null;
      }
      await api.updateOrg(payload);
      setMsg("Configuración de organización guardada con éxito.");
      setLlmApiKey("");
      await fetchData();
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
  };

  const handleArchiveAccount = async (accId: string) => {
    setArchiveMsg("");
    try {
      await api.archiveAdAccount(accId);
      setConfirmArchiveId(null);
      // Si era la cuenta activa, ya no debe quedar seleccionada.
      if (activeAdAccountId === accId) setActiveAdAccountId("");
      await fetchData();
    } catch (err: any) {
      setArchiveMsg(`Error: ${err.message}`);
    }
  };

  const handleRestoreAccount = async (accId: string) => {
    setArchiveMsg("");
    try {
      await api.restoreAdAccount(accId);
      await fetchData();
    } catch (err: any) {
      setArchiveMsg(`Error: ${err.message}`);
    }
  };

  const handleDeleteAccount = async (accId: string) => {
    setArchiveMsg("");
    try {
      await api.deleteAdAccount(accId);
      setDeleteConfirmId(null);
      setDeleteInputName("");
      if (activeAdAccountId === accId) setActiveAdAccountId("");
      await fetchData();
    } catch (err: any) {
      setArchiveMsg(`Error: ${err.message}`);
    }
  };

  const handleRefreshPages = async () => {
    setPagesMsg("");
    setRefreshingPages(true);
    try {
      const res = await api.refreshPages();
      setPagesMsg(res.message);
      // La tarea corre en background (Celery) y puede tardar varios segundos
      // (una llamada a Meta por fuente + por página): re-consultar en varios
      // intentos en vez de un único timeout adivinado.
      const attempts = [5000, 10000, 16000];
      attempts.forEach((delay, i) => {
        setTimeout(async () => {
          try {
            setPages(await api.getMyPages());
            if (i === attempts.length - 1) setPagesMsg("Páginas actualizadas desde Meta.");
          } catch {
            /* la lista queda como estaba; el mensaje anterior sigue visible */
          } finally {
            if (i === attempts.length - 1) setRefreshingPages(false);
          }
        }, delay);
      });
    } catch (err: any) {
      setPagesMsg(`Error: ${err.message}`);
      setRefreshingPages(false);
    }
  };

  const handleSavePageWhatsApp = async (pageId: string) => {
    const value = (waInputs[pageId] || "").trim();
    if (!value) return;
    setPagesMsg("");
    setWaSavingId(pageId);
    try {
      await api.setPageWhatsApp(pageId, value);
      setWaInputs((prev) => ({ ...prev, [pageId]: "" }));
      const pagesData = await api.getMyPages();
      setPages(pagesData);
      // El backend lo agrega al catálogo: refrescar para verlo ahí también.
      setNumbers(await api.getWhatsAppNumbers());
    } catch (err: any) {
      setPagesMsg(`Error: ${err.message}`);
    } finally {
      setWaSavingId(null);
    }
  };

  const handleAddNumber = async () => {
    const value = newNumber.trim();
    if (!value) return;
    setPagesMsg("");
    setNumberSavingId("new");
    try {
      await api.addWhatsAppNumber(value);
      setNewNumber("");
      setNumbers(await api.getWhatsAppNumbers());
    } catch (err: any) {
      setPagesMsg(`Error: ${err.message}`);
    } finally {
      setNumberSavingId(null);
    }
  };

  const handleUpdateNumber = async (id: string) => {
    const value = (numberEdits[id] || "").trim();
    if (!value) return;
    setPagesMsg("");
    setNumberSavingId(id);
    try {
      await api.updateWhatsAppNumber(id, value);
      setNumberEdits((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      setNumbers(await api.getWhatsAppNumbers());
    } catch (err: any) {
      setPagesMsg(`Error: ${err.message}`);
    } finally {
      setNumberSavingId(null);
    }
  };

  const handleDeleteNumber = async (id: string) => {
    setPagesMsg("");
    setNumberSavingId(id);
    try {
      await api.deleteWhatsAppNumber(id);
      setNumbers(await api.getWhatsAppNumbers());
    } catch (err: any) {
      setPagesMsg(`Error: ${err.message}`);
    } finally {
      setNumberSavingId(null);
    }
  };

  const handleSetDefaultPage = async (accId: string, pageId: string) => {
    setPagesMsg("");
    setDefaultSavingId(accId);
    try {
      await api.setDefaultPage(accId, pageId || null);
      const accountsData = await api.getAdAccounts(true);
      setAccounts(accountsData);
    } catch (err: any) {
      setPagesMsg(`Error: ${err.message}`);
    } finally {
      setDefaultSavingId(null);
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
      setConnMsg("Cuenta conectada. Sincronización en curso en segundo plano.");
      setAccName("");
      setExtId("");
      setAccessToken("");
      fetchData();
    } catch (err: any) {
      setConnMsg(`Error: ${err.message}`);
    }
  };

  const handleReconnectAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reconnectingAccount) return;
    setReconnectLoading(true);
    setReconnectMsg("");
    try {
      await api.reconnectAdAccount(reconnectingAccount.id, reconnectToken);
      setReconnectMsg("Token reconectado exitosamente. Sincronización iniciada.");
      setReconnectToken("");
      setTimeout(() => {
        setReconnectingAccount(null);
        setReconnectMsg("");
      }, 1500);
      fetchData();
    } catch (err: any) {
      setReconnectMsg(`Error: ${err.message}`);
    } finally {
      setReconnectLoading(false);
    }
  };

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando configuración...</div>;
  }

  return (
    <div>
      <div style={{ marginBottom: "2rem" }}>
        <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Configuración del Sistema</h2>
        <p style={{ color: "var(--color-fog)", fontSize: "15px", marginTop: "4px" }}>Configura tus credenciales, cuentas publicitarias de Meta y límites financieros.</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1.8fr", gap: "2rem" }}>
        {/* Org Settings */}
        <div className="glass-card" style={{ height: "fit-content" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1.25rem" }}>Preferencias Generales</h3>
          
          {msg && (
            <div style={{
              background: "rgba(39, 166, 68, 0.1)",
              border: "1px solid rgba(39, 166, 68, 0.2)",
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
            {/* Proveedor y Credencial de IA */}
            <div style={{
              background: "rgba(255, 255, 255, 0.02)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "8px",
              padding: "1rem",
              display: "flex",
              flexDirection: "column",
              gap: "0.85rem",
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <label style={{ fontSize: "0.85rem", fontWeight: 510, color: "var(--color-paper)" }}>
                  Proveedor de IA (LLM)
                </label>
                {org?.has_llm_api_key && (
                  <span style={{
                    fontSize: "11px",
                    padding: "2px 8px",
                    borderRadius: "4px",
                    background: "rgba(56, 189, 248, 0.12)",
                    color: "var(--color-mint)",
                    border: "1px solid rgba(56, 189, 248, 0.25)"
                  }}>
                    {org.llm_api_key_preview}
                  </span>
                )}
              </div>

              <select
                className="form-input"
                value={llmProvider}
                onChange={(e) => handleProviderChange(e.target.value)}
                style={{ background: "var(--color-obsidian)" }}
              >
                <option value="">Predeterminado del sistema ({aiProvider ? ({ openai: "OpenAI", gemini_direct: "Gemini", openrouter: "OpenRouter" }[aiProvider] ?? aiProvider) : "Automático"})</option>
                <option value="openai">OpenAI (GPT-5.4 / GPT-5.4 Mini)</option>
                <option value="gemini_direct">Google Gemini (Directo)</option>
                <option value="openrouter">OpenRouter (Multi-modelo)</option>
              </select>

              {llmProvider && (
                <div>
                  <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                    API Key de {llmProvider === "openai" ? "OpenAI" : llmProvider === "gemini_direct" ? "Google Gemini" : "OpenRouter"}
                  </label>
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <input
                      type="password"
                      className="form-input"
                      value={llmApiKey}
                      onChange={(e) => {
                        setLlmApiKey(e.target.value);
                        setLlmKeyTestMsg(null);
                      }}
                      placeholder={org?.has_llm_api_key ? "•••••••••••• (dejar en blanco para no cambiar)" : "sk-..."}
                      style={{ flex: 1 }}
                    />
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={handleTestLlmKey}
                      disabled={testingLlmKey || (!llmApiKey && !org?.has_llm_api_key)}
                      style={{ fontSize: "12px", whiteSpace: "nowrap", padding: "0.4rem 0.75rem" }}
                    >
                      {testingLlmKey ? "Probando..." : "Probar"}
                    </button>
                  </div>
                  {llmKeyTestMsg && (
                    <div style={{
                      marginTop: "0.5rem",
                      fontSize: "12px",
                      color: llmKeyTestMsg.valid ? "var(--success)" : "var(--color-coral-red)",
                    }}>
                      {badgeIcon(llmKeyTestMsg.valid ? "SUCCESS" : "ERROR")} {llmKeyTestMsg.text}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                Modelo de IA Preferido
              </label>
              <select
                className="form-input"
                value={aiModel}
                onChange={(e) => setAiModel(e.target.value)}
                style={{ background: "var(--color-obsidian)" }}
              >
                {aiModels.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem", display: "block" }}>
                Solo se muestran los modelos compatibles con el proveedor seleccionado.
              </span>
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

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                Rubro del negocio
              </label>
              <select
                className="form-input"
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
                style={{ background: "var(--color-obsidian)" }}
              >
                <option value="">Sin especificar</option>
                {industries.map((i) => (
                  <option key={i.value} value={i.value}>{i.label}</option>
                ))}
              </select>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem", display: "block" }}>
                Determina qué recordatorios de políticas de Meta se muestran en el Dashboard.
              </span>
            </div>

            <button type="submit" className="btn-secondary" style={{ alignSelf: "flex-start" }}>
              Guardar Cambios
            </button>
          </form>
        </div>

        {/* Ad Accounts Connections */}
        <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
          
          {/* List connected */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1rem" }}>Cuentas Publicitarias Conectadas</h3>
            {archiveMsg && (
              <div style={{
                background: "rgba(235, 87, 87, 0.1)", border: "1px solid rgba(235, 87, 87, 0.2)",
                color: "var(--danger)", padding: "0.5rem 0.75rem", borderRadius: "6px",
                fontSize: "0.85rem", marginBottom: "1rem",
              }}>
                {archiveMsg}
              </div>
            )}
            {accounts.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No tienes ninguna cuenta activa conectada.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {accounts.map((acc) => {
                  const isActive = acc.id === activeAdAccountId;
                  return (
                    <div
                      key={acc.id}
                      className="card-subtle"
                      onClick={() => setActiveAdAccountId(acc.id)}
                      title="Establecer como cuenta activa"
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        cursor: "pointer",
                        borderColor: isActive ? "var(--color-signal-teal)" : undefined,
                        flexWrap: "wrap",
                        gap: "0.75rem",
                      }}
                    >
                      <div>
                        <strong style={{ color: "var(--color-paper)", fontWeight: 510, fontSize: "14px" }}>
                          {acc.name}
                          {isActive && (
                            <span style={{ color: "var(--color-signal-teal)", fontSize: "12px", marginLeft: "8px", fontWeight: 510 }}>
                              ✓ Activa
                            </span>
                          )}
                        </strong>
                        <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                          Meta Ads ID: <code>{acc.external_account_id}</code>
                        </p>
                        {acc.status === "EXPIRED" && (
                          <p style={{ fontSize: "12px", color: "var(--color-coral-red)", marginTop: "0.35rem" }}>
                            El token expiró. Reconecta la cuenta abajo con un token nuevo.
                          </p>
                        )}
                        {acc.last_sync_error_code && acc.status !== "EXPIRED" && (
                          <div style={{
                            marginTop: "6px",
                            padding: "6px 10px",
                            borderRadius: "6px",
                            background: "rgba(242, 153, 74, 0.1)",
                            border: "1px solid rgba(242, 153, 74, 0.25)",
                            fontSize: "12px",
                            color: "var(--color-solar-amber)",
                          }}>
                            <strong>Diagnóstico de sync (Error {acc.last_sync_error_code}):</strong> {acc.last_sync_error_detail}
                          </div>
                        )}
                      </div>

                      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                        {acc.status === "EXPIRED" ? (
                          <span className="badge badge-danger">{badgeIcon("badge-danger")} Token expirado</span>
                        ) : acc.status === "SUSPENDED" ? (
                          <span className="badge badge-warning">{badgeIcon("badge-warning")} Suspendida</span>
                        ) : (
                          <span className="badge badge-success">{badgeIcon("badge-success")} Activo</span>
                        )}

                        {(acc.status === "EXPIRED" || acc.last_sync_error_code === 190) && (
                          <button
                            type="button"
                            className="btn-primary btn-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setReconnectingAccount(acc);
                              setReconnectToken("");
                              setReconnectMsg("");
                            }}
                          >
                            Reconectar
                          </button>
                        )}

                        {confirmArchiveId === acc.id ? (
                          <>
                            <span style={{ fontSize: "12px", color: "var(--color-coral-red)" }}>¿Archivar {acc.name}?</span>
                            <button
                              type="button"
                              className="btn-secondary btn-sm"
                              onClick={(e) => { e.stopPropagation(); handleArchiveAccount(acc.id); }}
                            >
                              Sí, archivar
                            </button>
                            <button
                              type="button"
                              className="btn-secondary btn-sm"
                              onClick={(e) => { e.stopPropagation(); setConfirmArchiveId(null); }}
                            >
                              Cancelar
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="btn-secondary btn-sm"
                            onClick={(e) => { e.stopPropagation(); setConfirmArchiveId(acc.id); }}
                          >
                            Archivar
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Cuentas archivadas (0.4) */}
            {archivedAccounts.length > 0 && (
              <div style={{ marginTop: "1.25rem", borderTop: "1px solid var(--color-graphite)", paddingTop: "1rem" }}>
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={() => setShowArchived(!showArchived)}
                  style={{ width: "100%", justifyContent: "space-between", display: "flex", alignItems: "center" }}
                >
                  <span>Cuentas archivadas ({archivedAccounts.length})</span>
                  <span>{showArchived ? "▲ Ocultar" : "▼ Ver"}</span>
                </button>

                {showArchived && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginTop: "1rem" }}>
                    {archivedAccounts.map((acc) => {
                      const isDeleting = deleteConfirmId === acc.id;
                      return (
                        <div
                          key={acc.id}
                          className="card-subtle"
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.5rem",
                            padding: "0.75rem 1rem",
                            opacity: 0.85,
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
                            <div>
                              <strong style={{ color: "var(--color-paper)", fontWeight: 510, fontSize: "14px" }}>
                                {acc.name}
                              </strong>
                              <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                                Meta Ads ID: <code>{acc.external_account_id}</code> · <span className="badge">Archivada</span>
                              </p>
                            </div>
                            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                onClick={() => handleRestoreAccount(acc.id)}
                              >
                                Restaurar
                              </button>
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                onClick={() => {
                                  setDeleteConfirmId(acc.id);
                                  setDeleteInputName("");
                                }}
                                style={{ borderColor: "rgba(235, 87, 87, 0.3)", color: "var(--color-coral-red)" }}
                              >
                                Eliminar definitivamente
                              </button>
                            </div>
                          </div>

                          {isDeleting && (
                            <div style={{
                              marginTop: "0.5rem",
                              padding: "0.75rem",
                              background: "rgba(235, 87, 87, 0.08)",
                              borderRadius: "6px",
                              border: "1px solid rgba(235, 87, 87, 0.2)"
                            }}>
                              <p style={{ fontSize: "13px", color: "var(--color-coral-red)", marginBottom: "0.5rem" }}>
                                ⚠️ Esta acción es irreversible y eliminará todo el historial asociado. Escribe <strong>{acc.name}</strong> para confirmar:
                              </p>
                              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                                <input
                                  type="text"
                                  className="form-input"
                                  style={{ padding: "4px 8px", fontSize: "13px", maxWidth: "220px" }}
                                  placeholder={acc.name}
                                  value={deleteInputName}
                                  onChange={(e) => setDeleteInputName(e.target.value)}
                                />
                                <button
                                  type="button"
                                  className="btn-danger btn-sm"
                                  disabled={deleteInputName !== acc.name}
                                  onClick={() => handleDeleteAccount(acc.id)}
                                >
                                  Confirmar eliminación
                                </button>
                                <button
                                  type="button"
                                  className="btn-secondary btn-sm"
                                  onClick={() => {
                                    setDeleteConfirmId(null);
                                    setDeleteInputName("");
                                  }}
                                >
                                  Cancelar
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Páginas de Facebook + WhatsApp */}
          <div className="glass-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
              <h3 style={{ fontSize: "16px", fontWeight: 510 }}>Páginas de Facebook y WhatsApp</h3>
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={handleRefreshPages}
                disabled={refreshingPages}
              >
                {refreshingPages ? "Actualizando..." : "↻ Actualizar páginas desde Meta"}
              </button>
            </div>

            {pagesMsg && (
              <div style={{
                background: pagesMsg.includes("Error") ? "rgba(235, 87, 87, 0.1)" : "rgba(39, 166, 68, 0.1)",
                border: `1px solid ${pagesMsg.includes("Error") ? "rgba(235, 87, 87, 0.2)" : "rgba(39, 166, 68, 0.2)"}`,
                color: pagesMsg.includes("Error") ? "var(--danger)" : "var(--success)",
                padding: "0.5rem 0.75rem", borderRadius: "6px", fontSize: "0.85rem", marginBottom: "1rem",
              }}>
                {pagesMsg}
              </div>
            )}

            {pages.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>
                No hay páginas descubiertas todavía. Si acabas de otorgar acceso a una página en el
                Business Manager, pulsa &quot;Actualizar páginas desde Meta&quot;.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {pages.map((p) => (
                  <div key={p.id} className="card-subtle" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
                    <div>
                      <strong style={{ color: "var(--color-paper)", fontWeight: 510, fontSize: "14px" }}>{p.name}</strong>
                      <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                        ID: <code>{p.external_page_id}</code>
                      </p>
                    </div>
                    {/* El campo se muestra SIEMPRE, tenga número o no: antes
                        solo aparecía cuando estaba vacío, así que un número mal
                        tipeado quedaba congelado sin forma de corregirlo. */}
                    <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                      {p.whatsapp_number ? (
                        <span className="badge badge-success">{badgeIcon("badge-success")} WA {p.whatsapp_number}</span>
                      ) : (
                        <span className="badge badge-warning">{badgeIcon("badge-warning")} Sin WhatsApp</span>
                      )}
                      {/* El "Ej." del placeholder no es cosmético: sin él, un
                          número peruano bien formado se lee como un valor ya
                          cargado y la persona clickea "Guardar" —deshabilitado
                          porque el input está vacío— creyendo que lo guardó. */}
                      <input
                        type="text"
                        className="form-input"
                        style={{ width: "160px", padding: "0.35rem 0.5rem", fontSize: "0.85rem" }}
                        placeholder={p.whatsapp_number ? "Cambiar por otro" : "Ej. 51999888777"}
                        aria-label={`Número de WhatsApp de ${p.name}`}
                        value={waInputs[p.id] ?? ""}
                        onChange={(e) => setWaInputs((prev) => ({ ...prev, [p.id]: e.target.value }))}
                      />
                      <button
                        type="button"
                        className="btn-secondary btn-sm"
                        disabled={waSavingId === p.id || !(waInputs[p.id] || "").trim()}
                        onClick={() => handleSavePageWhatsApp(p.id)}
                      >
                        {waSavingId === p.id ? "Guardando..." : p.whatsapp_number ? "Cambiar" : "Guardar"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Catálogo de números (F1): varios por organización, editables.
                Es la lista que ofrece el formulario de creación. */}
            <div style={{ marginTop: "1.5rem" }}>
              <h4 style={{ fontSize: "14px", fontWeight: 510, marginBottom: "0.5rem" }}>
                Números de WhatsApp disponibles
              </h4>
              <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
                Los números entre los que se puede elegir al crear una campaña. Los marcados
                como <strong>Meta</strong> se descubren solos y los actualiza la sincronización;
                los manuales se pueden editar y borrar.
              </p>

              <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem", marginBottom: "0.85rem" }}>
                {numbers.map((n) => {
                  const isMeta = n.source === "META";
                  const editing = numberEdits[n.id] !== undefined;
                  return (
                    <div key={n.id} className="card-subtle" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.6rem" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                        {editing ? (
                          <input
                            type="text"
                            className="form-input"
                            style={{ width: "170px", padding: "0.35rem 0.5rem", fontSize: "0.85rem" }}
                            aria-label={`Editar número ${n.display_number}`}
                            value={numberEdits[n.id]}
                            onChange={(e) => setNumberEdits((prev) => ({ ...prev, [n.id]: e.target.value }))}
                          />
                        ) : (
                          <strong style={{ color: "var(--color-paper)", fontWeight: 510, fontSize: "14px" }}>
                            {n.display_number}
                          </strong>
                        )}
                        <span className={isMeta ? "badge badge-success" : "badge"}>
                          {isMeta ? "Meta" : "Manual"}
                        </span>
                        {n.verified_name && (
                          <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>{n.verified_name}</span>
                        )}
                      </div>

                      {!isMeta && (
                        <div style={{ display: "flex", gap: "0.4rem" }}>
                          {editing ? (
                            <>
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                disabled={numberSavingId === n.id || !numberEdits[n.id].trim()}
                                onClick={() => handleUpdateNumber(n.id)}
                              >
                                {numberSavingId === n.id ? "Guardando..." : "Guardar"}
                              </button>
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                onClick={() => setNumberEdits((prev) => {
                                  const next = { ...prev };
                                  delete next[n.id];
                                  return next;
                                })}
                              >
                                Cancelar
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                onClick={() => setNumberEdits((prev) => ({ ...prev, [n.id]: n.display_number }))}
                              >
                                Editar
                              </button>
                              <button
                                type="button"
                                className="btn-secondary btn-sm"
                                disabled={numberSavingId === n.id}
                                onClick={() => handleDeleteNumber(n.id)}
                              >
                                Eliminar
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
                {numbers.length === 0 && (
                  <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>
                    Todavía no hay números. Agrega uno abajo para poder crear campañas.
                  </p>
                )}
              </div>

              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                <input
                  type="text"
                  className="form-input"
                  style={{ width: "200px", padding: "0.4rem 0.6rem", fontSize: "0.9rem" }}
                  placeholder="Ej. 51999888777"
                  aria-label="Nuevo número de WhatsApp"
                  value={newNumber}
                  onChange={(e) => setNewNumber(e.target.value)}
                />
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  disabled={numberSavingId === "new" || !newNumber.trim()}
                  onClick={handleAddNumber}
                >
                  {numberSavingId === "new" ? "Agregando..." : "Agregar número"}
                </button>
              </div>
            </div>

            {pages.length > 0 && (
              <div style={{ marginTop: "1.25rem" }}>
                <h4 style={{ fontSize: "14px", fontWeight: 510, marginBottom: "0.5rem" }}>Página predeterminada por cuenta</h4>
                <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
                  La página que la IA y el formulario de creación usan por defecto para las campañas de cada cuenta.
                  Meta no expone el WhatsApp de las páginas automáticamente en la mayoría de tokens — si una página
                  aparece &quot;Sin WhatsApp&quot;, ingresa el número arriba una sola vez.
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                  {accounts.filter((a) => a.status !== "ARCHIVED").map((acc) => (
                    <div key={acc.id} style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "0.85rem", color: "var(--color-paper)", minWidth: "180px" }}>{acc.name}</span>
                      <select
                        className="form-input"
                        style={{ background: "var(--color-obsidian)", maxWidth: "320px", padding: "0.35rem 0.5rem", fontSize: "0.85rem" }}
                        value={acc.default_page_id || ""}
                        disabled={defaultSavingId === acc.id}
                        onChange={(e) => handleSetDefaultPage(acc.id, e.target.value)}
                      >
                        <option value="">— Sin página predeterminada —</option>
                        {pages.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name} {p.whatsapp_number ? `(WA ${p.whatsapp_number})` : "(sin WhatsApp)"}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Reconectar cuenta EXPIRED (4.4) */}
          {reconnectingAccount && (
            <div className="glass-card" style={{ border: "1px solid var(--color-coral-red)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
                <h3 style={{ fontSize: "16px", fontWeight: 510, color: "var(--color-coral-red)" }}>
                  Reconectar Token: {reconnectingAccount.name}
                </h3>
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={() => {
                    setReconnectingAccount(null);
                    setReconnectToken("");
                    setReconnectMsg("");
                  }}
                >
                  ✕ Cerrar
                </button>
              </div>

              <p style={{ fontSize: "13px", color: "var(--color-fog)", marginBottom: "1rem" }}>
                Pega un nuevo token de acceso para la cuenta <code>{reconnectingAccount.external_account_id}</code>. El token se canjeará a larga duración y se reactivará la sincronización.
              </p>

              {reconnectMsg && (
                <div style={{
                  background: reconnectMsg.includes("Error") ? "rgba(235, 87, 87, 0.1)" : "rgba(39, 166, 68, 0.1)",
                  border: `1px solid ${reconnectMsg.includes("Error") ? "rgba(235, 87, 87, 0.2)" : "rgba(39, 166, 68, 0.2)"}`,
                  color: reconnectMsg.includes("Error") ? "var(--danger)" : "var(--success)",
                  padding: "0.5rem 0.75rem",
                  borderRadius: "6px",
                  fontSize: "0.85rem",
                  marginBottom: "1rem"
                }}>
                  {reconnectMsg}
                </div>
              )}

              <form onSubmit={handleReconnectAccount} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div>
                  <label style={{ display: "block", fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                    Nuevo Access Token de Meta Graph API
                  </label>
                  <input
                    type="password"
                    className="form-input"
                    required
                    value={reconnectToken}
                    onChange={(e) => setReconnectToken(e.target.value)}
                    placeholder="EAAB..."
                  />
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => {
                      setReconnectingAccount(null);
                      setReconnectToken("");
                      setReconnectMsg("");
                    }}
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    className="btn-primary btn-sm"
                    disabled={reconnectLoading || !reconnectToken.trim()}
                  >
                    {reconnectLoading ? "Verificando..." : "Guardar y Reactivar Cuenta"}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* Connect new */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "0.5rem" }}>Conectar Cuenta de Meta Ads</h3>
            <p style={{ color: "var(--color-fog)", fontSize: "13px", marginBottom: "1rem" }}>
              Conecta una cuenta publicitaria mediante su ID y token de acceso con permisos de gestión de anuncios.
            </p>

            {/* Guía de Scopes y Permisos (4.1) */}
            <div style={{
              background: "var(--color-obsidian)",
              border: "1px solid var(--color-graphite)",
              borderRadius: "8px",
              padding: "12px",
              marginBottom: "1.25rem",
              fontSize: "12px",
            }}>
              <strong style={{ color: "var(--color-paper)", display: "block", marginBottom: "4px" }}>
                💡 Permisos (Scopes) requeridos en Meta:
              </strong>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", margin: "6px 0" }}>
                {[
                  "ads_read", "ads_management", "business_management",
                  "pages_show_list", "pages_read_engagement", "pages_manage_ads",
                  "read_insights", "whatsapp_business_management"
                ].map((scope) => (
                  <code key={scope} style={{ background: "rgba(255,255,255,0.06)", padding: "2px 6px", borderRadius: "4px", color: "var(--color-signal-teal)" }}>
                    {scope}
                  </code>
                ))}
              </div>
              <p style={{ color: "var(--color-fog)", marginTop: "6px", margin: 0 }}>
                Genera tu token en <a href="https://developers.facebook.com/tools/explorer/" target="_blank" rel="noreferrer" style={{ color: "var(--color-signal-teal)", textDecoration: "underline" }}>Meta Graph API Explorer</a> o desde un Usuario del Sistema en Business Manager.
              </p>
            </div>
            
            {connMsg && (
              <div style={{
                background: connMsg.includes("Error") ? "rgba(235, 87, 87, 0.1)" : "rgba(39, 166, 68, 0.1)",
                border: `1px solid ${connMsg.includes("Error") ? "rgba(235, 87, 87, 0.2)" : "rgba(39, 166, 68, 0.2)"}`,
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
                    placeholder="act_123456789012345"
                  />
                  <span style={{ fontSize: "11px", color: "var(--color-fog)", marginTop: "2px", display: "block" }}>
                    Si escribes solo los dígitos, le añadimos <code>act_</code> automáticamente.
                  </span>
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
