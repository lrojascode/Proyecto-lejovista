import { badgeIcon, metaStatusBadge } from "@/lib/badge";

// UX-1: estado REAL de la campaña en Meta (ACTIVE/PAUSED/ARCHIVED/DELETED),
// un único componente usado en el dashboard y en la lista de campañas —
// antes el dashboard pintaba `campaign.status` crudo en inglés
// (ACTIVE→verde, resto→ámbar) mientras la lista de campañas ya usaba
// `metaStatusBadge` (español + distinción de 4 estados). Mismo concepto, dos
// lecturas visuales distintas.
export function StatusBadge({ status }: { status: string }) {
  const { cls, label } = metaStatusBadge(status);
  return (
    <span className={cls}>
      {badgeIcon(cls)} {label}
    </span>
  );
}
