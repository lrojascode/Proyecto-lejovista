import type { ChatRiskCard } from "@/lib/types";
import { RISK_STYLES } from "@/lib/badge";

export function RiskCard({
  card,
  onDecision,
  disabled,
}: {
  card: ChatRiskCard;
  onDecision: (decision: "accept" | "reject" | "change", text?: string) => void;
  disabled?: boolean;
}) {
  const style = RISK_STYLES[card.risk_level] || RISK_STYLES.MEDIUM;

  return (
    <div className="chat-bubble-row assistant">
      <div className="glass-card chat-card" style={{ borderLeft: `3px solid ${style.color}` }}>
        <h4 style={{ fontSize: "15px", fontWeight: 590, marginBottom: "4px" }}>
          Riesgo <span style={{ color: style.color }}>{style.label}</span>
        </h4>
        {(card.risk_reasons ?? []).length > 0 && (
          <ul style={{ marginTop: "8px", paddingLeft: "18px", fontSize: "13px", color: "var(--text-secondary)" }}>
            {(card.risk_reasons ?? []).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}

        {(card.preflight_checks ?? []).length > 0 && (
          <div style={{ marginTop: "12px" }}>
            <p className="chat-card-field-label" style={{ marginBottom: "4px" }}>Validación contra Meta</p>
            {(card.preflight_checks ?? []).map((c, i) => (
              <div key={i} className="chat-card-field">
                <span className="chat-card-field-label">{c.step}</span>
                <span className="chat-card-field-value">
                  {c.ok === true ? "✓ OK" : c.ok === false ? "✕ Rechazado" : c.skipped ? "— Omitido" : "—"}
                </span>
              </div>
            ))}
          </div>
        )}

        <p style={{ fontSize: "13px", color: "var(--text-secondary)", marginTop: "12px" }}>
          La campaña se crea siempre <strong>PAUSADA</strong> — vos decidís cuándo activarla.
          Si querés ajustar algo, escribilo abajo antes de aceptar.
        </p>

        <div className="chat-card-actions">
          {card.actions.map((a) => (
            <button
              key={a.action}
              type="button"
              disabled={disabled}
              className={a.style === "primary" ? "btn-primary" : a.style === "danger" ? "btn-danger" : "btn-secondary"}
              onClick={() => onDecision(a.action)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
