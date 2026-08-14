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

  const fetchData = async () => {
    try {
      const orgData = await api.getOrg();
      const accountsData = await api.getAdAccounts(true); // incluye archivadas, para poder restaurarlas
      setOrg(orgData);
      setAccounts(accountsData);
      setMaxBudget(orgData.max_budget);
      setIndustry(orgData.industry || "");

      const pagesData = await api.getMyPages();
      setPages(pagesData);

      setNumbers(await api.getWhatsAppNumbers());

      const policiesData = await api.getBusinessPolicies();
      setIndustries(policiesData.available_industries || []);

      // Solo se ofrecen los modelos del proveedor de IA realmente activo
      // (según qué API key esté configurada en el backend), para no guardar
      // un modelo que el proveedor activo no reconocería.
      const modelsData = await api.getAiModels();
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

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.updateOrg({ ai_model: aiModel, max_budget: maxBudget, industry: industry || null });
      setMsg("Configuración de organización guardada con éxito.");
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
              {aiProvider && (
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem", display: "block" }}>
                  Proveedor activo detectado: <strong>{
                    { openai: "OpenAI", gemini_direct: "Gemini (directo)", openrouter: "OpenRouter" }[aiProvider] ?? aiProvider
                  }</strong>. Solo se muestran los modelos que este proveedor soporta.
                </span>
              )}
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
              <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No tienes ninguna cuenta conectada.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {accounts.map((acc) => {
                  const isActive = acc.id === activeAdAccountId;
                  const isArchived = acc.status === "ARCHIVED";
                  return (
                    <div
                      key={acc.id}
                      className="card-subtle"
                      onClick={() => !isArchived && setActiveAdAccountId(acc.id)}
                      title={isArchived ? undefined : "Establecer como cuenta activa"}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        cursor: isArchived ? "default" : "pointer",
                        opacity: isArchived ? 0.6 : 1,
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
                        {isArchived && (
                          <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "0.35rem" }}>
                            Archivada — oculta del selector y de "Crear campaña". Su historial se conserva intacto.
                          </p>
                        )}
                      </div>

                      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                        {isArchived ? (
                          <span className="badge">Archivada</span>
                        ) : acc.status === "EXPIRED" ? (
                          <span className="badge badge-danger">{badgeIcon("badge-danger")} Token expirado</span>
                        ) : acc.status === "SUSPENDED" ? (
                          <span className="badge badge-warning">{badgeIcon("badge-warning")} Suspendida</span>
                        ) : (
                          <span className="badge badge-success">{badgeIcon("badge-success")} Activo</span>
                        )}

                        {isArchived ? (
                          <button
                            type="button"
                            className="btn-secondary btn-sm"
                            onClick={(e) => { e.stopPropagation(); handleRestoreAccount(acc.id); }}
                          >
                            Restaurar
                          </button>
                        ) : confirmArchiveId === acc.id ? (
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

          {/* Connect new */}
          <div className="glass-card">
            <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "1.25rem" }}>Conectar Cuenta de Meta Ads</h3>
            
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
