// 5.4(b) (E-6): los badges de estado (éxito/alerta/error) se distinguían
// solo por color — invisible para daltonismo o pantallas en escala de
// grises. Un ícono fijo por clase añade una segunda señal redundante.
export function badgeIcon(cls: string): string {
  if (cls.includes("badge-success")) return "✓";
  if (cls.includes("badge-warning")) return "⚠";
  if (cls.includes("badge-danger")) return "✕";
  if (cls.includes("badge-ai")) return "✦";
  return "";
}

// Estado REAL de la campaña en Meta (ACTIVE/PAUSED/ARCHIVED/DELETED), tal
// como lo sincroniza el backend en `CampaignSnapshot.status`. No confundir
// con un badge de rendimiento (CPL/conversiones) — eso es una evaluación de
// la IA sobre qué tan bien va la campaña, no si está prendida o apagada en
// Meta. Antes `campaigns/page.tsx` mostraba el badge de rendimiento bajo la
// columna "Estado", haciendo ver como "apagada"/"fallida" a una campaña que
// en Meta seguía activa (o viceversa).
// Colores por nivel de riesgo de creación de campaña (paleta "midnight").
// Usado tanto por el formulario (campaigns/new) como por el chat de
// creación — una sola copia, para que el nivel de riesgo se vea igual
// sin importar por qué puerta se creó la campaña.
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export const RISK_STYLES: Record<string, { bg: string; border: string; color: string; label: string }> = {
  LOW: { bg: "rgba(39, 174, 96, 0.1)", border: "rgba(39, 174, 96, 0.25)", color: "var(--color-pulse-green)", label: "BAJO" },
  MEDIUM: { bg: "rgba(242, 201, 76, 0.1)", border: "rgba(242, 201, 76, 0.25)", color: "#f2c94c", label: "MEDIO" },
  HIGH: { bg: "rgba(235, 87, 87, 0.1)", border: "rgba(235, 87, 87, 0.25)", color: "var(--color-coral-red)", label: "ALTO" },
};

export function metaStatusBadge(status: string): { cls: string; label: string } {
  switch (status) {
    case "ACTIVE":
      return { cls: "badge badge-success", label: "Activa" };
    case "PAUSED":
      return { cls: "badge", label: "Pausada" };
    case "ARCHIVED":
      return { cls: "badge", label: "Archivada" };
    case "DELETED":
      return { cls: "badge badge-danger", label: "Eliminada" };
    default:
      return { cls: "badge", label: status || "Desconocido" };
  }
}
