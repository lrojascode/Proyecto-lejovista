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
  // Con el write-back apagado la campaña no existe en Meta: el link llevaría a
  // un Ads Manager donde no hay nada que ver, y el check verde diría que salió
  // todo bien. Se ocultan los dos.
  const simulated = message.simulated === true;

  const adsManagerLink =
    !simulated && externalAccountId && message.campaign_id
      ? `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${externalAccountId.replace(/^act_/, "")}` +
        `&selected_campaign_ids=${message.campaign_id}`
      : null;

  return (
    <div className="chat-bubble-row assistant">
      <div className="glass-card chat-card">
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
          {simulated ? (
            <span aria-hidden style={{ fontSize: "18px", color: "var(--color-solar-amber)" }}>⚠</span>
          ) : (
            <CheckIcon size={20} style={{ color: "var(--color-pulse-green)" }} />
          )}
          <h4 style={{ fontSize: "15px", fontWeight: 590 }}>
            {simulated ? "Simulación — no se creó en Meta" : message.campaign_name || "Campaña creada"}
          </h4>
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
