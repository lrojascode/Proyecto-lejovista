import type { ChatResultMessage } from "@/lib/types";
import { CheckIcon } from "@/components/icons";

export function ResultCard({
  message,
  externalAccountId,
  onCreateAnother,
}: {
  message: ChatResultMessage;
  externalAccountId?: string;
  onCreateAnother: () => void;
}) {
  const adsManagerLink =
    externalAccountId && message.campaign_id
      ? `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${externalAccountId.replace(/^act_/, "")}` +
        `&selected_campaign_ids=${message.campaign_id}`
      : null;

  return (
    <div className="chat-bubble-row assistant">
      <div className="glass-card chat-card">
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
          <CheckIcon size={20} style={{ color: "var(--color-pulse-green)" }} />
          <h4 style={{ fontSize: "15px", fontWeight: 590 }}>{message.campaign_name || "Campaña creada"}</h4>
        </div>
        <p style={{ fontSize: "14px", color: "var(--text-primary)" }}>{message.text}</p>
        <div className="chat-card-actions">
          {adsManagerLink && (
            <a href={adsManagerLink} target="_blank" rel="noopener noreferrer" className="btn-secondary">
              Ver en Ads Manager
            </a>
          )}
          <button type="button" className="btn-primary" onClick={onCreateAnother}>
            Crear otra campaña
          </button>
        </div>
      </div>
    </div>
  );
}
