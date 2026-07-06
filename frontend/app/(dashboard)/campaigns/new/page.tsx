"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAdAccount } from "@/components/providers/AdAccountProvider";

type MetaPage = {
  id: string;
  external_page_id: string;
  name: string;
  whatsapp_number: string | null;
};

type Suggestion = {
  risk_level: string;
  has_history: boolean;
  suggested_budget: number;
  suggested_days: number;
  excluded_placements: string[];
  best_device: string | null;
  best_age_range: string | null;
  winning_campaign: { name: string; cost_per_conv: number; ctr: number } | null;
  evidence: string[];
  copy: { headline: string; primary_text: string; description: string; welcome_message: string };
};

type DraftStatus = "idle" | "submitting" | "polling" | "success" | "error";

const POLL_INTERVAL_MS = 2500;
const POLL_MAX_ATTEMPTS = 40; // ~100s de margen

export default function NewCampaignPage() {
  const router = useRouter();
  const { activeAdAccountId, accounts } = useAdAccount();

  const [pages, setPages] = useState<MetaPage[]>([]);
  const [selectedPageId, setSelectedPageId] = useState("");

  const [formData, setFormData] = useState({
    product_name: "",
    zone: "",
    budget: 100,
    days: 7,
    image_url: "",
    image_hash: "",
    copy_text: "",
  });

  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState("");

  const [waInput, setWaInput] = useState("");
  const [waSaving, setWaSaving] = useState(false);
  const [waError, setWaError] = useState("");

  const [status, setStatus] = useState<DraftStatus>("idle");
  const [draftId, setDraftId] = useState<string | null>(null);
  const [riskLevel, setRiskLevel] = useState<string | null>(null);
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

  const handleSuggest = async () => {
    if (!activeAdAccountId) {
      setSuggestError("Selecciona una cuenta de anuncios activa primero.");
      return;
    }
    if (!formData.product_name || !formData.zone) {
      setSuggestError("Completa producto y zona antes de pedir la sugerencia.");
      return;
    }
    setSuggestLoading(true);
    setSuggestError("");
    try {
      const data: Suggestion = await api.getCampaignSuggestion(
        activeAdAccountId, formData.product_name, formData.zone
      );
      setSuggestion(data);
      setFormData((prev) => ({
        ...prev,
        budget: data.suggested_budget,
        days: data.suggested_days,
        copy_text: data.copy.primary_text,
      }));
    } catch (err: any) {
      setSuggestError(err.message || "No se pudo obtener la sugerencia.");
    } finally {
      setSuggestLoading(false);
    }
  };

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
    if (!formData.image_url && !formData.image_hash) {
      setErrorMessage("Agrega una URL de imagen o un image_hash.");
      setStatus("error");
      return;
    }

    setStatus("submitting");
    setErrorMessage("");

    try {
      const res = await api.createCampaign({
        ad_account_id: activeAdAccountId,
        page_id: selectedPage.external_page_id,
        product_name: formData.product_name,
        zone: formData.zone,
        budget: formData.budget,
        days: formData.days,
        whatsapp_number: selectedPage.whatsapp_number,
        image_url: formData.image_url || undefined,
        image_hash: formData.image_hash || undefined,
        copy_text: formData.copy_text,
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
              <label style={labelStyle}>Zona</label>
              <input type="text" name="zone" required value={formData.zone} onChange={handleChange} className="form-input" placeholder="Surquillo" />
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
              <label style={labelStyle}>URL de imagen</label>
              <input type="text" name="image_url" value={formData.image_url} onChange={handleChange} className="form-input" placeholder="https://..." />
            </div>
            <div>
              <label style={labelStyle}>Image Hash (si ya está subida)</label>
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
            </div>
          )}

          <div>
            <label style={labelStyle}>Copy (editable)</label>
            <textarea name="copy_text" required value={formData.copy_text} onChange={handleChange} rows={6} className="form-input" placeholder="¡Hook, beneficios y llamada a la acción!"></textarea>
          </div>

          <button
            type="submit"
            disabled={!activeAdAccountId}
            className="btn-primary"
            style={{ width: "100%" }}
          >
            Aprobar y Crear Campaña (PAUSADA)
          </button>
        </form>
      )}
    </div>
  );
}
