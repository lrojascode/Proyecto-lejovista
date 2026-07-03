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

  return (
    <div className="max-w-3xl mx-auto p-6 bg-white dark:bg-gray-800 rounded-lg shadow mt-8">
      <h1 className="text-2xl font-bold mb-6">Crear Nueva Campaña (Flujo Asíncrono)</h1>
      
      {status === "error" && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          Error: {errorMessage}
        </div>
      )}
      
      {(status === "polling" || status === "success") ? (
        <div className="flex flex-col items-center justify-center py-12">
          {status === "polling" ? (
            <div className="animate-spin rounded-full h-16 w-16 border-t-2 border-b-2 border-blue-500 mb-4"></div>
          ) : (
            <div className="h-16 w-16 text-green-500 mb-4 text-6xl">✓</div>
          )}
          <h2 className="text-xl font-semibold">
            {status === "polling" ? "Procesando en Celery Chain..." : "¡Creada (Pausada en Meta)!"}
          </h2>
          <p className="text-gray-500 mt-2">Draft ID: {jobId}</p>
          
          {status === "success" && (
            <button 
              onClick={() => router.push("/campaigns")}
              className="mt-6 bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded"
            >
              Volver a Campañas
            </button>
          )}
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Ad Account ID</label>
              <input type="text" name="ad_account_id" required value={formData.ad_account_id} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Page ID</label>
              <input type="text" name="page_id" required value={formData.page_id} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Producto</label>
              <input type="text" name="product_name" required value={formData.product_name} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" placeholder="Cable GPT" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Zona</label>
              <input type="text" name="zone" required value={formData.zone} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" placeholder="Surquillo" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Presupuesto</label>
              <input type="number" name="budget" required value={formData.budget} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Días</label>
              <input type="number" name="days" required value={formData.days} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Número WhatsApp</label>
              <input type="text" name="whatsapp_number" required value={formData.whatsapp_number} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" placeholder="51999888777" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Image Hash (opcional)</label>
              <input type="text" name="image_hash" value={formData.image_hash} onChange={handleChange} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" />
            </div>
          </div>
          
          <div>
            <label className="block text-sm font-medium mb-1">Copy Text</label>
            <textarea name="copy_text" required value={formData.copy_text} onChange={handleChange} rows={5} className="w-full p-2 border rounded dark:bg-gray-700 dark:border-gray-600" placeholder="¡Hook, beneficios y llamada a la acción!"></textarea>
          </div>
          
          <button 
            type="submit" 
            disabled={status === "submitting"}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded disabled:opacity-50"
          >
            {status === "submitting" ? "Validando en API..." : "Aprobar y Crear Campaña"}
          </button>
        </form>
      )}
    </div>
  );
}
