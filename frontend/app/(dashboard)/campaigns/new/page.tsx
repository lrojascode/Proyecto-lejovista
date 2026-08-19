"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAdAccount } from "@/components/providers/AdAccountProvider";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import type { MetaPage, WhatsAppNumber, Suggestion } from "@/lib/types";
import { RISK_STYLES } from "@/lib/badge";

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
    budget_type: "LIFETIME" as "LIFETIME" | "DAILY",
    days: 7,
    image_path: "",
    image_hash: "",
    video_path: "",
    copy_text: "",
    headline: "",
    description: "",
  });

  // 3.4: variante de copy elegida (0 o 1) de las 2 que genera la IA.
  const [copyVariant, setCopyVariant] = useState(0);

  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageUploading, setImageUploading] = useState(false);
  const [imageUploadError, setImageUploadError] = useState("");
  const [videoName, setVideoName] = useState("");
  const [videoUploading, setVideoUploading] = useState(false);
  // F6: al subir una imagen el copy se regenera solo, viéndola.
  const [copyRegenerating, setCopyRegenerating] = useState(false);

  // Catálogo de números de la organización (F1).
  const [numbers, setNumbers] = useState<WhatsAppNumber[]>([]);
  const [selectedNumber, setSelectedNumber] = useState("");

  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState("");
  const [productSuggestedFromImage, setProductSuggestedFromImage] = useState(false);

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
  // El servidor puede tener las escrituras a Meta apagadas: la Saga termina
  // igual y el draft queda CREATED, pero en Ads Manager no hay nada. Se muestra
  // en vez de festejar una campaña que no existe.
  const [resultSimulated, setResultSimulated] = useState(false);
  // Qué variable exactamente lo está apagando: hay tres causas posibles
  // (MOCK_SERVICES, ENABLE_META_WRITEBACK, el allowlist) y desde la pantalla
  // eran indistinguibles.
  const [resultSimulatedReason, setResultSimulatedReason] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const pollAttempts = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api.getMyPages()
      .then((data: MetaPage[]) => {
        setPages(data);
        // Preselección: 1º la página predeterminada de la cuenta activa
        // (Settings → Páginas y WhatsApp); 2º, si solo hay una página, esa.
        const activeAccount = accounts.find((a) => a.id === activeAdAccountId);
        const defaultId = activeAccount?.default_page_id;
        // Guard funcional: no pisar una selección manual si el efecto se
        // re-ejecuta (ej. el provider refresca `accounts`).
        setSelectedPageId((prev) => {
          if (prev) return prev;
          if (defaultId && data.some((p) => p.id === defaultId)) return defaultId;
          if (data.length === 1) return data[0].id;
          return prev;
        });
      })
      .catch((e: any) => console.error("Error cargando páginas:", e));

    api.getWhatsAppNumbers()
      .then((data: WhatsAppNumber[]) => setNumbers(data))
      .catch((e: any) => console.error("Error cargando números:", e));

    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [activeAdAccountId, accounts]);

  // El número de la página elegida manda como preselección; si no tiene, el
  // primero del catálogo. Sin pisar una elección manual del usuario.
  useEffect(() => {
    setSelectedNumber((prev) => {
      if (prev) return prev;
      const pageNumber = pages.find((p) => p.id === selectedPageId)?.whatsapp_number;
      if (pageNumber) return pageNumber;
      return numbers[0]?.display_number || "";
    });
  }, [pages, selectedPageId, numbers]);

  const selectedPage = pages.find((p) => p.id === selectedPageId) || null;
  const activeAccount = accounts.find((a) => a.id === activeAdAccountId) || null;
  // Lo que realmente se gastará en toda la campaña; es la cifra con la que
  // razonan el riesgo y el aprendizaje, sea cual sea el modo elegido.
  const totalBudget = formData.budget_type === "DAILY"
    ? Number((formData.budget * formData.days).toFixed(2))
    : formData.budget;

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
    // Imagen y video son excluyentes en el creativo de Meta.
    setFormData((prev) => ({ ...prev, image_path: "", video_path: "" }));
    setVideoName("");
    setImageUploadError("");
    setImageUploading(true);
    try {
      const res = await api.uploadCampaignImage(file);
      setFormData((prev) => ({ ...prev, image_path: res.image_path }));
      // F6: regenerar el copy viendo la foto real, sin que haya que pedirlo.
      await regenerateCopyWithImage(res.image_path);
    } catch (err: any) {
      setImageUploadError(err.message || "No se pudo subir la imagen.");
    } finally {
      setImageUploading(false);
    }
  };

  const handleVideoFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    setImagePreviewUrl(null);
    setFormData((prev) => ({ ...prev, image_path: "", video_path: "" }));
    setVideoName(file.name);
    setImageUploadError("");
    setVideoUploading(true);
    try {
      const res = await api.uploadCampaignVideo(file);
      setFormData((prev) => ({ ...prev, video_path: res.video_path }));
    } catch (err: any) {
      setImageUploadError(err.message || "No se pudo subir el video.");
      setVideoName("");
    } finally {
      setVideoUploading(false);
    }
  };

  /** Vuelve a pedir el copy y sugerencia pasando la imagen. Si el producto
   *  está vacío, prellena el nombre propuesto por la IA a partir de la foto. */
  const regenerateCopyWithImage = async (imagePath: string) => {
    if (!activeAdAccountId) return;
    setCopyRegenerating(true);
    try {
      const currentProd = formData.product_name.trim();
      const data: Suggestion = await api.getCampaignSuggestion(
        activeAdAccountId, currentProd || "Producto", imagePath
      );
      setSuggestion(data);
      setCopyVariant(0);

      const suggestedProd = data.copy?.suggested_product_name || data.suggested_product_name;
      setFormData((prev) => {
        const shouldFillProduct = !prev.product_name.trim() && Boolean(suggestedProd);
        if (shouldFillProduct) {
          setProductSuggestedFromImage(true);
        }
        return {
          ...prev,
          product_name: shouldFillProduct ? (suggestedProd as string) : prev.product_name,
          copy_text: data.copy.primary_text[0],
          headline: data.copy.headline[0],
          description: data.copy.description,
        };
      });
    } catch (err: any) {
      console.error("No se pudo regenerar el copy con la imagen:", err);
    } finally {
      setCopyRegenerating(false);
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
        activeAdAccountId, formData.product_name, formData.image_path || undefined
      );
      setSuggestion(data);
      setCopyVariant(0);
      setFormData((prev) => ({
        ...prev,
        // La IA siempre sugiere un TOTAL; si el usuario eligió diario, se
        // reparte entre los días.
        budget: prev.budget_type === "DAILY"
          ? Number((data.suggested_budget / data.suggested_days).toFixed(2))
          : data.suggested_budget,
        days: data.suggested_days,
        copy_text: data.copy.primary_text[0],
        headline: data.copy.headline[0],
        description: data.copy.description,
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
      .then((draft) => {
        if (draft.status === "CREATED") {
          // payload es JSON libre (Dict[str, Any] en el backend) — result se
          // va llenando paso a paso por la Saga, sin forma fija que declarar.
          const payload = draft.payload as
            { result?: { campaign_id?: string; simulated?: boolean; simulated_reason?: string } } | undefined;
          setResultCampaignId(payload?.result?.campaign_id || null);
          setResultSimulated(Boolean(payload?.result?.simulated));
          setResultSimulatedReason(payload?.result?.simulated_reason || null);
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
    if (!selectedNumber) {
      setErrorMessage("Elige un número de WhatsApp para la campaña.");
      setStatus("error");
      return;
    }
    if (!formData.image_path && !formData.image_hash && !formData.video_path) {
      setErrorMessage("Sube una imagen o un video, o pega un image_hash.");
      setStatus("error");
      return;
    }
    if (imageUploading || videoUploading) {
      setErrorMessage("Espera a que termine de subirse el archivo.");
      setStatus("error");
      return;
    }

    setRiskChecking(true);
    setErrorMessage("");
    try {
      // El riesgo se evalúa sobre el TOTAL, igual que el backend.
      const risk = await api.previewCampaignRisk(activeAdAccountId, totalBudget, formData.copy_text);
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
      // La descripción es única (no tiene variantes), pero se re-aplica por si
      // el usuario la había borrado.
      description: prev.description || suggestion.copy.description,
    }));
  };

  // Paso 2 (Ajuste 1): el usuario confirmó el riesgo → se crea de verdad.
  const doCreate = async () => {
    if (!activeAdAccountId || !selectedPage || !selectedNumber) return;
    setPendingRisk(null);
    setStatus("submitting");
    setErrorMessage("");

    try {
      const res = await api.createCampaign({
        ad_account_id: activeAdAccountId,
        page_id: selectedPage.external_page_id,
        product_name: formData.product_name,
        budget: formData.budget,
        budget_type: formData.budget_type,
        days: formData.days,
        whatsapp_number: selectedNumber,
        image_path: formData.image_path || undefined,
        image_hash: formData.image_hash || undefined,
        video_path: formData.video_path || undefined,
        copy_text: formData.copy_text,
        headline: formData.headline || undefined,
        description: formData.description || undefined,
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
            <div style={{
              fontSize: "40px", marginBottom: "16px",
              color: resultSimulated ? "var(--color-solar-amber)" : "var(--color-pulse-green)",
            }}>{resultSimulated ? "⚠" : "✓"}</div>
          )}
          <h2 style={{ fontSize: "18px", fontWeight: 510 }}>
            {status === "submitting" && "Validando contra Meta..."}
            {status === "polling" && "Creando campaña (pausada) en Meta..."}
            {status === "success" && (resultSimulated
              ? "Simulación — la campaña NO se creó en Meta"
              : "¡Campaña creada — en estado PAUSADA!")}
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
                {resultSimulated ? (
                  <>
                    La campaña quedó guardada acá pero no existe en Ads Manager. El presupuesto, el
                    copy y la segmentación sí son reales: al encender el write-back, se crea de
                    verdad.
                    {resultSimulatedReason && (
                      <>
                        <br />
                        <span className="mono" style={{ fontSize: "13px", color: "var(--color-solar-amber)" }}>
                          Motivo: {resultSimulatedReason}
                        </span>
                      </>
                    )}
                  </>
                ) : (
                  <>
                    Recuerda: 1) si tu cuenta tiene el límite de gasto topado, súbelo en Meta; 2) revisa la campaña en
                    Ads Manager; 3) actívala manualmente cuando esté todo correcto.
                  </>
                )}
              </p>
              {adsManagerLink && !resultSimulated && (
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
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <label style={labelStyle}>Producto</label>
                {productSuggestedFromImage && (
                  <span style={{ fontSize: "11px", color: "var(--color-mint)", fontWeight: 500 }}>
                    ✨ Propuesto a partir de tu imagen (editable)
                  </span>
                )}
              </div>
              <input
                type="text"
                name="product_name"
                required
                value={formData.product_name}
                onChange={(e) => {
                  setProductSuggestedFromImage(false);
                  handleChange(e);
                }}
                className="form-input"
                placeholder="Cable GPT"
              />
            </div>
            <div>
              <label style={labelStyle} htmlFor="wa-number">Número de WhatsApp</label>
              <select
                id="wa-number"
                className="form-input"
                value={selectedNumber}
                onChange={(e) => setSelectedNumber(e.target.value)}
                style={{ background: "var(--color-obsidian)" }}
                required
              >
                <option value="">Selecciona un número...</option>
                {numbers.map((n) => (
                  <option key={n.id} value={n.display_number}>
                    {n.display_number}{n.verified_name ? ` — ${n.verified_name}` : ""}
                  </option>
                ))}
              </select>
              {numbers.length === 0 && (
                <span style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "4px", display: "block" }}>
                  No hay números cargados — agrégalos en Configuración.
                </span>
              )}
            </div>
            <div>
              <label style={labelStyle} htmlFor="budget-type">Tipo de presupuesto</label>
              <select
                id="budget-type"
                className="form-input"
                value={formData.budget_type}
                onChange={(e) => setFormData((prev) => ({
                  ...prev,
                  budget_type: e.target.value as "LIFETIME" | "DAILY",
                  // Se convierte el monto al cambiar de modo para que el gasto
                  // total no salte sin que el usuario lo note.
                  budget: e.target.value === "DAILY"
                    ? Number((prev.budget / prev.days).toFixed(2))
                    : Number((prev.budget * prev.days).toFixed(2)),
                }))}
                style={{ background: "var(--color-obsidian)" }}
              >
                <option value="LIFETIME">Total de la campaña</option>
                <option value="DAILY">Diario</option>
              </select>
            </div>
            <div>
              <label style={labelStyle}>
                Presupuesto (S/. {formData.budget_type === "DAILY" ? "por día" : "total"})
              </label>
              <input type="number" name="budget" required min={1} step="0.01" value={formData.budget} onChange={handleChange} className="form-input" />
              {formData.budget_type === "DAILY" && (
                <span style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "4px", display: "block" }}>
                  Total en {formData.days} días: <strong>S/. {totalBudget.toFixed(2)}</strong>
                </span>
              )}
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
              {copyRegenerating && (
                <span style={{ fontSize: "12px", color: "var(--color-lavender)", marginTop: "4px", display: "block" }}>
                  ✨ Regenerando el copy con la imagen...
                </span>
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
              <label style={labelStyle}>Video del anuncio (alternativa a la imagen)</label>
              <input type="file" accept="video/mp4,video/quicktime" onChange={handleVideoFileChange} className="form-input" />
              {videoUploading && (
                <span style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "4px", display: "block" }}>
                  Subiendo video...
                </span>
              )}
              {!videoUploading && formData.video_path && (
                <span style={{ fontSize: "12px", color: "var(--color-pulse-green)", marginTop: "4px", display: "block" }}>
                  ✓ Video subido ({videoName}) — Meta lo procesará al crear la campaña
                </span>
              )}
              <span style={{ fontSize: "12px", color: "var(--color-fog)", marginTop: "4px", display: "block" }}>
                MP4 o MOV, hasta 100MB. La miniatura la genera Meta.
              </span>
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
              {suggestion.suggested_campaign_name && (
                <p style={{ marginTop: "8px" }}>
                  Nombre de la campaña: <strong>{suggestion.suggested_campaign_name}</strong>
                </p>
              )}
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

          {/* Título y descripción del anuncio: se pedían desde hace tiempo pero
              no llegaban al creativo, así que el anuncio salía solo con el copy. */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px" }}>
            <div>
              <label style={labelStyle} htmlFor="headline">
                Título del anuncio ({formData.headline.length}/40)
              </label>
              <input
                id="headline" type="text" name="headline" maxLength={40}
                value={formData.headline} onChange={handleChange}
                className="form-input" placeholder="Cable GPT 🔥"
              />
            </div>
            <div>
              <label style={labelStyle} htmlFor="description">
                Descripción ({formData.description.length}/30)
              </label>
              <input
                id="description" type="text" name="description" maxLength={30}
                value={formData.description} onChange={handleChange}
                className="form-input" placeholder="Cotiza por WhatsApp"
              />
            </div>
          </div>

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
