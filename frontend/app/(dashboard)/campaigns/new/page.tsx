"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAdAccount } from "@/components/providers/AdAccountProvider";
import { useWebSocket } from "@/components/providers/WebSocketProvider";

type MetaPage = {
  id: string;
  external_page_id: string;
  name: string;
  whatsapp_number: string | null;
};

type Change = { change: string; why: string };
type Analysis = {
  summary: string;
  mode: string;
  converged: boolean;
  maturity_reason: string;
  strengths: string[];
  weaknesses: string[];
  changes_proposed: Change[];
  past_failures: string[];
  evaluation_window_days: number;
  confidence: string;
};

type Suggestion = {
  risk_level: string;
  risk_reasons: string[];
  has_history: boolean;
  maturity: { converged: boolean; mode: string; reason: string };
  analysis: Analysis;
  suggested_budget: number;
  suggested_days: number;
  excluded_placements: string[];
  best_device: string | null;
  best_age_range: string | null;
  winning_campaign: { name: string; cost_per_conv: number; ctr: number } | null;
  evidence: string[];
  // 3.4: 2 variantes de headline/primary_text — el usuario elige en la UI.
  copy: { headline: string[]; primary_text: string[]; description: string; welcome_message: string };
};

// Colores por nivel de riesgo (paleta del design system "midnight").
const RISK_STYLES: Record<string, { bg: string; border: string; color: string; label: string }> = {
  LOW: { bg: "rgba(39, 174, 96, 0.1)", border: "rgba(39, 174, 96, 0.25)", color: "var(--color-pulse-green)", label: "BAJO" },
  MEDIUM: { bg: "rgba(242, 201, 76, 0.1)", border: "rgba(242, 201, 76, 0.25)", color: "#f2c94c", label: "MEDIO" },
  HIGH: { bg: "rgba(235, 87, 87, 0.1)", border: "rgba(235, 87, 87, 0.25)", color: "var(--color-coral-red)", label: "ALTO" },
};

type DraftStatus = "idle" | "submitting" | "polling" | "success" | "error";

const POLL_INTERVAL_MS = 2500;
// D-3 (auditoría): la Saga tarda ~3 min (4 pasos con countdown=40s entre sí).
// Con 40 intentos (~100s) el polling se agotaba ANTES de que la creación pudiera
// terminar y el usuario veía timeout en creaciones exitosas. 120 ≈ 5 min de margen;
// además el evento WS `campaign_creation` (abajo) resuelve sin esperar al polling.
const POLL_MAX_ATTEMPTS = 120;

export default function NewCampaignPage() {
  const router = useRouter();
  const { activeAdAccountId, accounts } = useAdAccount();
  const { lastEvent } = useWebSocket();

  const [pages, setPages] = useState<MetaPage[]>([]);
  const [selectedPageId, setSelectedPageId] = useState("");

  const [formData, setFormData] = useState({
    product_name: "",
    budget: 100,
    days: 7,
    image_path: "",
    image_hash: "",
    copy_text: "",
    headline: "",
  });

  // 3.4: variante de copy elegida (0 o 1) de las 2 que genera la IA.
  const [copyVariant, setCopyVariant] = useState(0);

  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageUploading, setImageUploading] = useState(false);
  const [imageUploadError, setImageUploadError] = useState("");

  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState("");

  const [waInput, setWaInput] = useState("");
  const [waSaving, setWaSaving] = useState(false);
  const [waError, setWaError] = useState("");

  const [status, setStatus] = useState<DraftStatus>("idle");
  const [draftId, setDraftId] = useState<string | null>(null);
  const [riskLevel, setRiskLevel] = useState<string | null>(null);
  // Confirmación de riesgo ANTES de crear (Ajuste 1): al pedir crear, primero
  // calculamos el riesgo con los valores actuales y lo mostramos; el usuario
  // debe confirmar explícitamente.
  const [pendingRisk, setPendingRisk] = useState<{ level: string; reasons: string[] } | null>(null);
  const [riskChecking, setRiskChecking] = useState(false);
  const [resultCampaignId, setResultCampaignId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const pollAttempts = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api.getMyPages()
      .then((data: MetaPage[]) => {
        setPages(data);
        if (data.length === 1) setSelectedPageId(data[0].id);
      })
      .catch((e: any) => console.error("Error cargando páginas:", e));

    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  const selectedPage = pages.find((p) => p.id === selectedPageId) || null;
  const activeAccount = accounts.find((a) => a.id === activeAdAccountId) || null;

  const handleSaveWhatsApp = async () => {
    if (!selectedPage || !waInput.trim()) return;
    setWaSaving(true);
    setWaError("");
    try {
      const updated = await api.setPageWhatsApp(selectedPage.id, waInput.trim());
      setPages((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setWaInput("");
    } catch (err: any) {
      setWaError(err.message || "No se pudo guardar el número de WhatsApp.");
    } finally {
      setWaSaving(false);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: name === "budget" || name === "days" ? Number(value) : value }));
  };

  // Reemplaza el viejo campo "URL de imagen": la app guarda la foto y usa esa
  // ruta para crear la campaña — preview instantáneo local, subida en background.
  const handleImageFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    setImagePreviewUrl(URL.createObjectURL(file));
    setFormData((prev) => ({ ...prev, image_path: "" }));
    setImageUploadError("");
    setImageUploading(true);
    try {
      const res = await api.uploadCampaignImage(file);
      setFormData((prev) => ({ ...prev, image_path: res.image_path }));
    } catch (err: any) {
      setImageUploadError(err.message || "No se pudo subir la imagen.");
    } finally {
      setImageUploading(false);
    }
  };

  const handleSuggest = async () => {
    if (!activeAdAccountId) {
      setSuggestError("Selecciona una cuenta de anuncios activa primero.");
      return;
    }
    if (!formData.product_name) {
      setSuggestError("Completa el producto antes de pedir la sugerencia.");
      return;
    }
    setSuggestLoading(true);
    setSuggestError("");
    try {
      const data: Suggestion = await api.getCampaignSuggestion(
        activeAdAccountId, formData.product_name
      );
      setSuggestion(data);
      setCopyVariant(0);
      setFormData((prev) => ({
        ...prev,
        budget: data.suggested_budget,
        days: data.suggested_days,
        copy_text: data.copy.primary_text[0],
        headline: data.copy.headline[0],
      }));
    } catch (err: any) {
      setSuggestError(err.message || "No se pudo obtener la sugerencia.");
    } finally {
      setSuggestLoading(false);
    }
  };

  // D-3: el worker publica `campaign_creation` (READY/FAILED) por WebSocket al
  // terminar la Saga — antes se ignoraba y solo existía el polling. Al recibirlo,
  // consultamos el draft de inmediato (fuente de verdad) en vez de esperar el
  // próximo tick. El polling queda como fallback si el WS está caído.
  useEffect(() => {
    if (!lastEvent || lastEvent.event_type !== "campaign_creation") return;
    if (!draftId || lastEvent.job_id !== draftId) return;
    if (status !== "polling") return;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    pollDraft(draftId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent]);

  const pollDraft = (id: string) => {
    pollAttempts.current += 1;
    api.getCampaignDraft(id)
      .then((draft: any) => {
        if (draft.status === "CREATED") {
          setResultCampaignId(draft.payload?.result?.campaign_id || null);
          setStatus("success");
        } else if (draft.status === "FAILED") {
          setErrorMessage(draft.error_message || "La creación falló en Meta.");
          setStatus("error");
        } else if (pollAttempts.current >= POLL_MAX_ATTEMPTS) {
          setErrorMessage("La creación está tardando más de lo esperado. Revisa el estado más tarde.");
          setStatus("error");
        } else {
          pollTimer.current = setTimeout(() => pollDraft(id), POLL_INTERVAL_MS);
        }
      })
      .catch((err: any) => {
        setErrorMessage(err.message || "No se pudo consultar el estado de la creación.");
        setStatus("error");
      });
  };

  // Paso 1 (Ajuste 1): valida el formulario y calcula el nivel de riesgo con
  // los valores ACTUALES, sin crear todavía. Muestra la confirmación de riesgo.
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeAdAccountId) {
      setErrorMessage("Selecciona una cuenta de anuncios activa primero.");
      setStatus("error");
      return;
    }
    if (!selectedPage) {
      setErrorMessage("Selecciona una página de Meta conectada.");
      setStatus("error");
      return;
    }
    if (!selectedPage.whatsapp_number) {
      setErrorMessage("La página seleccionada no tiene un número de WhatsApp conectado.");
      setStatus("error");
      return;
    }
    if (!formData.image_path && !formData.image_hash) {
      setErrorMessage("Sube una imagen o pega un image_hash.");
      setStatus("error");
      return;
    }
    if (imageUploading) {
      setErrorMessage("Espera a que termine de subirse la imagen.");
      setStatus("error");
      return;
    }

    setRiskChecking(true);
    setErrorMessage("");
    try {
      const risk = await api.previewCampaignRisk(activeAdAccountId, formData.budget, formData.copy_text);
      setPendingRisk({ level: risk.risk_level, reasons: risk.risk_reasons });
    } catch (err: any) {
      // Si falla el cálculo de riesgo no bloqueamos la creación: mostramos el
      // riesgo como desconocido y dejamos confirmar igual.
      setPendingRisk({ level: "MEDIUM", reasons: ["No se pudo calcular el riesgo con precisión; revisa los parámetros antes de confirmar."] });
    } finally {
      setRiskChecking(false);
    }
  };

  // 3.4: cambia entre las 2 variantes de headline/primary_text que genera la IA.
  const selectCopyVariant = (index: 0 | 1) => {
    if (!suggestion) return;
    setCopyVariant(index);
    setFormData((prev) => ({
      ...prev,
      copy_text: suggestion.copy.primary_text[index],
      headline: suggestion.copy.headline[index],
    }));
  };

  // Paso 2 (Ajuste 1): el usuario confirmó el riesgo → se crea de verdad.
  const doCreate = async () => {
    if (!activeAdAccountId || !selectedPage || !selectedPage.whatsapp_number) return;
    setPendingRisk(null);
    setStatus("submitting");
    setErrorMessage("");

    try {
      const res = await api.createCampaign({
        ad_account_id: activeAdAccountId,
        page_id: selectedPage.external_page_id,
        product_name: formData.product_name,
        budget: formData.budget,
        days: formData.days,
        whatsapp_number: selectedPage.whatsapp_number,
        image_path: formData.image_path || undefined,
        image_hash: formData.image_hash || undefined,
        copy_text: formData.copy_text,
        headline: formData.headline || undefined,
      });

      setDraftId(res.draft_id);
      setRiskLevel(res.risk_level);
      setStatus("polling");
      pollAttempts.current = 0;
      pollDraft(res.draft_id);
    } catch (err: any) {
      setErrorMessage(err.message || "No se pudo iniciar la creación de la campaña.");
      setStatus("error");
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block", fontSize: "13px", color: "var(--color-fog)", marginBottom: "4px",
  };

  const adsManagerLink = (() => {
    if (!activeAccount || !resultCampaignId) return null;
    const actNumber = activeAccount.external_account_id.replace(/^act_/, "");
    return `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${actNumber}&selected_campaign_ids=${resultCampaignId}`;
  })();

  return (
    <div className="glass-card" style={{ maxWidth: "760px", margin: "32px auto 0 auto" }}>
      <h1 style={{ fontSize: "20px", fontWeight: 590, letterSpacing: "-0.012em", marginBottom: "8px" }}>
        Crear Nueva Campaña
      </h1>
      <p style={{ color: "var(--color-fog)", fontSize: "14px", marginBottom: "24px" }}>
        La IA sugiere los parámetros según el rendimiento real de tu cuenta. Revisa, edita si quieres, y aprueba —
        la campaña se crea siempre <strong>pausada</strong> en Meta; tú la activas manualmente en Ads Manager.
      </p>

      {!activeAdAccountId && (
        <div role="alert" style={{
          background: "rgba(235, 87, 87, 0.1)", border: "1px solid rgba(235, 87, 87, 0.2)",
          color: "var(--color-coral-red)", padding: "12px 16px", borderRadius: "6px", marginBottom: "16px", fontSize: "14px",
        }}>
          Selecciona una cuenta de anuncios activa desde el panel lateral antes de continuar.
        </div>
      )}

      {status === "error" && (
        <div role="alert" style={{
          background: "rgba(235, 87, 87, 0.1)", border: "1px solid rgba(235, 87, 87, 0.2)",
          color: "var(--color-coral-red)", padding: "12px 16px", borderRadius: "6px",
          marginBottom: "16px", fontSize: "14px",
        }}>
          Error: {errorMessage}
        </div>
      )}

      {(status === "submitting" || status === "polling" || status === "success") ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 0" }} aria-live="polite">
          {status !== "success" ? (
            <div style={{
              width: "48px", height: "48px", borderRadius: "9999px", marginBottom: "16px",
              border: "2px solid var(--color-graphite)", borderTopColor: "var(--color-mist)",
              animation: "spin 1s linear infinite",
            }}></div>
          ) : (
            <div style={{ fontSize: "40px", color: "var(--color-pulse-green)", marginBottom: "16px" }}>✓</div>
          )}
          <h2 style={{ fontSize: "18px", fontWeight: 510 }}>
            {status === "submitting" && "Validando contra Meta..."}
            {status === "polling" && "Creando campaña (pausada) en Meta..."}
            {status === "success" && "¡Campaña creada — en estado PAUSADA!"}
          </h2>
          {status === "polling" && (
            <p style={{ color: "var(--color-fog)", marginTop: "8px", fontSize: "13px", maxWidth: "420px", textAlign: "center" }}>
              La creación va por pasos (campaña → conjunto → creativo → anuncio) con pausas de
              seguridad entre cada uno: toma <strong>~3 minutos</strong>. Puedes dejar esta pantalla abierta.
            </p>
          )}
          {draftId && (
            <p className="mono" style={{ color: "var(--color-fog)", marginTop: "8px", fontSize: "13px" }}>
              Draft ID: {draftId}
            </p>
          )}
          {riskLevel && status !== "success" && (
            <p style={{ color: "var(--color-fog)", marginTop: "4px", fontSize: "13px" }}>
              Nivel de riesgo de esta operación: <strong>{riskLevel}</strong>
            </p>
          )}

          {status === "success" && (
            <div style={{ textAlign: "center", marginTop: "16px" }}>
              <p style={{ color: "var(--color-fog)", fontSize: "14px", maxWidth: "480px" }}>
                Recuerda: 1) si tu cuenta tiene el límite de gasto topado, súbelo en Meta; 2) revisa la campaña en
                Ads Manager; 3) actívala manualmente cuando esté todo correcto.
              </p>
              {adsManagerLink && (
                <a href={adsManagerLink} target="_blank" rel="noopener noreferrer" className="btn-secondary" style={{ marginTop: "16px", display: "inline-block" }}>
                  Abrir en Ads Manager
                </a>
              )}
              <div>
                <button onClick={() => router.push("/campaigns")} className="btn-primary" style={{ marginTop: "16px" }}>
                  Volver a Campañas
                </button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px" }}>
            <div>
              <label style={labelStyle}>Página de Meta / WhatsApp</label>
              <select
                className="form-input"
                value={selectedPageId}
                onChange={(e) => setSelectedPageId(e.target.value)}
                style={{ background: "var(--color-obsidian)" }}
                required
              >
                <option value="">Selecciona una página...</option>
                {pages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} {p.whatsapp_number ? `(WA ${p.whatsapp_number})` : "(sin WhatsApp)"}
                  </option>
                ))}
              </select>
              {pages.length === 0 && (
                <span style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "4px", display: "block" }}>
                  No hay páginas descubiertas todavía — sincroniza tu cuenta primero.
                </span>
              )}
              {selectedPage && !selectedPage.whatsapp_number && (
                <div style={{ marginTop: "8px" }}>
                  <span style={{ fontSize: "12px", color: "var(--color-fog)", display: "block", marginBottom: "4px" }}>
                    Meta no expone el WhatsApp de esta página automáticamente (el token no tiene el permiso
                    whatsapp_business_management). Ingrésalo a mano una vez:
                  </span>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <input
                      type="text"
                      className="form-input"
                      placeholder="51999888777"
                      value={waInput}
                      onChange={(e) => setWaInput(e.target.value)}
                      style={{ flex: 1 }}
                    />
                    <button type="button" onClick={handleSaveWhatsApp} disabled={waSaving || !waInput.trim()} className="btn-secondary">
                      {waSaving ? "Guardando..." : "Guardar"}
                    </button>
                  </div>
                  {waError && (
                    <p role="alert" style={{ color: "var(--color-coral-red)", fontSize: "12px", marginTop: "4px" }}>{waError}</p>
                  )}
                </div>
              )}
            </div>
            <div>
              <label style={labelStyle}>Producto</label>
              <input type="text" name="product_name" required value={formData.product_name} onChange={handleChange} className="form-input" placeholder="Cable GPT" />
            </div>
            <div>
              <label style={labelStyle}>Presupuesto (S/. total)</label>
              <input type="number" name="budget" required min={1} value={formData.budget} onChange={handleChange} className="form-input" />
            </div>
            <div>
              <label style={labelStyle}>Días</label>
              <input type="number" name="days" required min={1} value={formData.days} onChange={handleChange} className="form-input" />
            </div>
            <div>
              <label style={labelStyle}>Imagen del anuncio</label>
              <input type="file" accept="image/jpeg,image/png,image/webp" onChange={handleImageFileChange} className="form-input" />
              {imageUploading && (
                <span style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "4px", display: "block" }}>
                  Subiendo imagen...
                </span>
              )}
              {!imageUploading && formData.image_path && (
                <span style={{ fontSize: "12px", color: "var(--color-pulse-green)", marginTop: "4px", display: "block" }}>
                  ✓ Imagen subida
                </span>
              )}
              {imageUploadError && (
                <p role="alert" style={{ color: "var(--color-coral-red)", fontSize: "12px", marginTop: "4px" }}>{imageUploadError}</p>
              )}
              {imagePreviewUrl && (
                <img
                  src={imagePreviewUrl}
                  alt="Previsualización de la imagen del anuncio"
                  style={{ marginTop: "8px", maxHeight: "140px", borderRadius: "6px", border: "1px solid var(--color-graphite)" }}
                />
              )}
            </div>
            <div>
              <label style={labelStyle}>Image Hash (avanzado — reutilizar una imagen ya subida a Meta)</label>
              <input type="text" name="image_hash" value={formData.image_hash} onChange={handleChange} className="form-input" />
            </div>
          </div>

          <div>
            <button type="button" onClick={handleSuggest} disabled={suggestLoading} className="btn-secondary">
              {suggestLoading ? "Analizando tu cuenta..." : "✨ Sugerir con IA"}
            </button>
            {suggestError && (
              <p role="alert" style={{ color: "var(--color-coral-red)", fontSize: "13px", marginTop: "8px" }}>{suggestError}</p>
            )}
          </div>

          {suggestion && (
            <div style={{
              background: "rgba(2, 184, 204, 0.08)", border: "1px solid rgba(2, 184, 204, 0.2)",
              borderRadius: "8px", padding: "16px", fontSize: "13px",
            }}>
              <p style={{ fontWeight: 590, marginBottom: "8px" }}>
                Sugerencia basada en {suggestion.has_history ? "el histórico real de tu cuenta" : "valores por defecto (sin histórico todavía)"}:
              </p>
              <ul style={{ paddingLeft: "18px", color: "var(--color-fog)", display: "flex", flexDirection: "column", gap: "4px" }}>
                {suggestion.evidence.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
              {suggestion.excluded_placements.length > 0 && (
                <p style={{ marginTop: "8px" }}>
                  Placements a excluir: <strong>{suggestion.excluded_placements.join(", ")}</strong>
                </p>
              )}
              {suggestion.risk_level && (
                <p style={{ marginTop: "8px" }}>
                  Nivel de riesgo estimado:{" "}
                  <strong style={{ color: (RISK_STYLES[suggestion.risk_level] || RISK_STYLES.MEDIUM).color }}>
                    {(RISK_STYLES[suggestion.risk_level] || RISK_STYLES.MEDIUM).label}
                  </strong>{" "}
                  <span style={{ color: "var(--color-fog)" }}>
                    (se recalcula con tu presupuesto final antes de confirmar)
                  </span>
                </p>
              )}

              {/* 3.4: elegir entre las 2 variantes de copy que generó la IA. */}
              {suggestion.copy?.headline?.length > 1 && (
                <div style={{ marginTop: "12px" }}>
                  <p style={{ marginBottom: "6px" }}>Variante de copy:</p>
                  <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                    {suggestion.copy.headline.map((h, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => selectCopyVariant(i as 0 | 1)}
                        style={{
                          textAlign: "left", flex: "1 1 200px", padding: "8px 10px",
                          borderRadius: "6px", cursor: "pointer",
                          border: copyVariant === i ? "1px solid var(--color-signal-teal)" : "1px solid var(--border-color)",
                          background: copyVariant === i ? "rgba(2, 184, 204, 0.12)" : "transparent",
                          color: "inherit", fontSize: "12px",
                        }}
                      >
                        <strong>Variante {i + 1}{copyVariant === i ? " ✓" : ""}</strong>
                        <div style={{ color: "var(--color-fog)", marginTop: "2px" }}>{h}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {suggestion?.analysis && (
            <div style={{
              background: "rgba(139, 92, 246, 0.07)", border: "1px solid rgba(139, 92, 246, 0.2)",
              borderRadius: "8px", padding: "16px", fontSize: "13px",
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", marginBottom: "8px", flexWrap: "wrap" }}>
                <p style={{ fontWeight: 590, margin: 0 }}>🧠 Qué está haciendo la IA</p>
                <span style={{
                  fontSize: "11px", fontWeight: 600, padding: "2px 8px", borderRadius: "9999px",
                  background: suggestion.analysis.converged ? "rgba(39, 174, 96, 0.15)" : "rgba(2, 184, 204, 0.15)",
                  color: suggestion.analysis.converged ? "var(--color-pulse-green)" : "var(--color-mist)",
                }}>
                  {suggestion.analysis.converged ? "MODO OPTIMIZADO" : "MODO EXPLORACIÓN"} · confianza {suggestion.analysis.confidence}
                </span>
              </div>

              <p style={{ color: "var(--color-fog)", marginBottom: "10px" }}>{suggestion.analysis.summary}</p>
              <p style={{ color: "var(--color-fog)", marginBottom: "10px", fontStyle: "italic" }}>{suggestion.analysis.maturity_reason}</p>

              {suggestion.analysis.changes_proposed.length > 0 && (
                <div style={{ marginBottom: "10px" }}>
                  <p style={{ fontWeight: 590, marginBottom: "4px" }}>Cambios que propone:</p>
                  <ul style={{ paddingLeft: "18px", color: "var(--color-fog)", display: "flex", flexDirection: "column", gap: "4px" }}>
                    {suggestion.analysis.changes_proposed.map((c, i) => (
                      <li key={i}><strong>{c.change}</strong> — {c.why}</li>
                    ))}
                  </ul>
                </div>
              )}

              {suggestion.analysis.past_failures.length > 0 && (
                <div style={{ marginBottom: "10px" }}>
                  <p style={{ fontWeight: 590, marginBottom: "4px" }}>Aprendizajes de fallos pasados:</p>
                  <ul style={{ paddingLeft: "18px", color: "var(--color-fog)", display: "flex", flexDirection: "column", gap: "4px" }}>
                    {suggestion.analysis.past_failures.map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                </div>
              )}

              <p style={{ color: "var(--color-fog)" }}>
                📆 Días de evaluación recomendados: <strong>{suggestion.analysis.evaluation_window_days}</strong> — deja correr la campaña ese tiempo antes de juzgar su rendimiento.
              </p>
              <p style={{ marginTop: "10px", marginBottom: 0 }}>
                <a href="/campaigns/learning" style={{ color: "var(--color-signal-teal)", fontSize: "13px" }}>
                  Ver el historial completo de aprendizaje de la IA →
                </a>
              </p>
            </div>
          )}

          <div>
            <label style={labelStyle}>Copy (editable)</label>
            <textarea name="copy_text" required value={formData.copy_text} onChange={handleChange} rows={6} className="form-input" placeholder="¡Hook, beneficios y llamada a la acción!"></textarea>
          </div>

          {pendingRisk ? (
            (() => {
              const rs = RISK_STYLES[pendingRisk.level] || RISK_STYLES.MEDIUM;
              return (
                <div style={{ background: rs.bg, border: `1px solid ${rs.border}`, borderRadius: "8px", padding: "16px" }}>
                  <p style={{ fontWeight: 590, marginBottom: "8px", display: "flex", alignItems: "center", gap: "8px" }}>
                    Nivel de riesgo de esta campaña:
                    <span style={{ color: rs.color, fontWeight: 700 }}>{rs.label}</span>
                  </p>
                  <ul style={{ paddingLeft: "18px", color: "var(--color-fog)", fontSize: "13px", display: "flex", flexDirection: "column", gap: "4px", marginBottom: "12px" }}>
                    {pendingRisk.reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                  <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                    <button type="button" onClick={doCreate} className="btn-primary" style={{ flex: 1, minWidth: "180px" }}>
                      Entiendo el riesgo — Crear Campaña (PAUSADA)
                    </button>
                    <button type="button" onClick={() => setPendingRisk(null)} className="btn-secondary">
                      Cancelar
                    </button>
                  </div>
                </div>
              );
            })()
          ) : (
            <button
              type="submit"
              disabled={!activeAdAccountId || riskChecking || imageUploading}
              className="btn-primary"
              style={{ width: "100%" }}
            >
              {riskChecking ? "Calculando nivel de riesgo..." : imageUploading ? "Subiendo imagen..." : "Revisar riesgo y crear campaña"}
            </button>
          )}
        </form>
      )}
    </div>
  );
}
