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
