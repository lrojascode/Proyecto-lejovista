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
