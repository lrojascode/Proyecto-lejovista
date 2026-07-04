"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function NewCampaignPage() {
  const router = useRouter();
  
  const [formData, setFormData] = useState({
    ad_account_id: "",
    page_id: "",
    product_name: "",
    zone: "",
    budget: 100,
    days: 7,
    whatsapp_number: "",
    image_url: "",
    image_hash: "",
    copy_text: ""
  });
  
  const [status, setStatus] = useState<"idle" | "submitting" | "polling" | "success" | "error">("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState("");

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: name === "budget" || name === "days" ? Number(value) : value }));
  };

  const pollStatus = async (id: string) => {
    try {
      // Usar endpoint dummy para polling de campaña (podemos usar el de sync o crear uno para draft status)
      // Asumiremos que el backend en Session 4 tendrá el /campaigns/drafts/{id}/status, pero por ahora mostramos UI optimista
      // Mocking polling for session 3 frontend verification
      let attempts = 0;
      const interval = setInterval(() => {
        attempts++;
        if (attempts > 3) {
          clearInterval(interval);
          setStatus("success");
        }
      }, 2000);
    } catch (e) {
      console.error(e);
      setStatus("error");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus("submitting");
    setErrorMessage("");

    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${apiUrl}/api/v1/campaigns/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Agregar auth si lo requiere el API, por ahora es un mock
        },
        body: JSON.stringify(formData)
      });

      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Failed to create campaign");
      }

      const data = await response.json();
      setJobId(data.draft_id);
      setStatus("polling");
      pollStatus(data.draft_id);
      
    } catch (err: any) {
      setErrorMessage(err.message);
      setStatus("error");
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block", fontSize: "13px", color: "var(--color-fog)", marginBottom: "4px"
  };

  return (
    <div className="glass-card" style={{ maxWidth: "720px", margin: "32px auto 0 auto" }}>
      <h1 style={{ fontSize: "20px", fontWeight: 590, letterSpacing: "-0.012em", marginBottom: "24px" }}>Crear Nueva Campaña (Flujo Asíncrono)</h1>

      {status === "error" && (
        <div style={{
          background: "rgba(235, 87, 87, 0.1)", border: "1px solid rgba(235, 87, 87, 0.2)",
          color: "var(--color-coral-red)", padding: "12px 16px", borderRadius: "6px",
          marginBottom: "16px", fontSize: "14px"
        }}>
          Error: {errorMessage}
        </div>
      )}

      {(status === "polling" || status === "success") ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 0" }}>
          {status === "polling" ? (
            <div style={{
              width: "48px", height: "48px", borderRadius: "9999px", marginBottom: "16px",
              border: "2px solid var(--color-graphite)", borderTopColor: "var(--color-mist)",
              animation: "spin 1s linear infinite"
            }}></div>
          ) : (
            <div style={{ fontSize: "40px", color: "var(--color-pulse-green)", marginBottom: "16px" }}>✓</div>
          )}
          <h2 style={{ fontSize: "18px", fontWeight: 510 }}>
            {status === "polling" ? "Procesando en Celery Chain..." : "¡Creada (Pausada en Meta)!"}
          </h2>
          <p className="mono" style={{ color: "var(--color-fog)", marginTop: "8px" }}>Draft ID: {jobId}</p>

          {status === "success" && (
            <button
              onClick={() => router.push("/campaigns")}
              className="btn-secondary"
              style={{ marginTop: "24px" }}
            >
              Volver a Campañas
            </button>
          )}
        </div>
      ) : (
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px" }}>
            <div>
              <label style={labelStyle}>Ad Account ID</label>
              <input type="text" name="ad_account_id" required value={formData.ad_account_id} onChange={handleChange} className="form-input" />
            </div>
            <div>
              <label style={labelStyle}>Page ID</label>
              <input type="text" name="page_id" required value={formData.page_id} onChange={handleChange} className="form-input" />
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
              <label style={labelStyle}>Presupuesto</label>
              <input type="number" name="budget" required value={formData.budget} onChange={handleChange} className="form-input" />
            </div>
            <div>
              <label style={labelStyle}>Días</label>
              <input type="number" name="days" required value={formData.days} onChange={handleChange} className="form-input" />
            </div>
            <div>
              <label style={labelStyle}>Número WhatsApp</label>
              <input type="text" name="whatsapp_number" required value={formData.whatsapp_number} onChange={handleChange} className="form-input" placeholder="51999888777" />
            </div>
            <div>
              <label style={labelStyle}>Image Hash (opcional)</label>
              <input type="text" name="image_hash" value={formData.image_hash} onChange={handleChange} className="form-input" />
            </div>
          </div>

          <div>
            <label style={labelStyle}>Copy Text</label>
            <textarea name="copy_text" required value={formData.copy_text} onChange={handleChange} rows={5} className="form-input" placeholder="¡Hook, beneficios y llamada a la acción!"></textarea>
          </div>

          <button
            type="submit"
            disabled={status === "submitting"}
            className="btn-primary"
            style={{ width: "100%" }}
          >
            {status === "submitting" ? "Validando en API..." : "Aprobar y Crear Campaña"}
          </button>
        </form>
      )}
    </div>
  );
}
