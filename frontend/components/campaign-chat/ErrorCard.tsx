import type { ChatErrorMessage } from "@/lib/types";
import { WarningIcon } from "@/components/icons";

export function ErrorCard({
  message,
  onAction,
}: {
  message: ChatErrorMessage;
  onAction: (action: "accept" | "reject" | "change") => void;
}) {
  return (
    <div className="chat-bubble-row assistant">
      <div className="glass-card chat-card" style={{ borderLeft: "3px solid var(--color-coral-red)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "4px" }}>
          <WarningIcon size={18} style={{ color: "var(--color-coral-red)" }} />
          <span style={{ fontSize: "13px", color: "var(--color-fog)" }}>
            {message.code === "CAP_REACHED" ? "Límite alcanzado" : "Algo no salió bien"}
          </span>
        </div>
        <p style={{ fontSize: "14px", color: "var(--text-primary)" }}>{message.text}</p>
        {message.actions && message.actions.length > 0 && (
          <div className="chat-card-actions">
            {message.actions.map((a) => (
              <button
                key={a.action}
                type="button"
                className={a.style === "primary" ? "btn-primary" : a.style === "danger" ? "btn-danger" : "btn-secondary"}
                onClick={() => onAction(a.action)}
              >
                {a.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
