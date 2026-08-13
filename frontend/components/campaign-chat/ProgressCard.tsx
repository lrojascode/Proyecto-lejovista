import type { ChatProgressMessage } from "@/lib/types";
import { SpinnerIcon } from "@/components/icons";

export function ProgressCard({ message }: { message: ChatProgressMessage }) {
  return (
    <div className="chat-bubble-row assistant">
      <div className="glass-card chat-card" style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <SpinnerIcon size={20} />
        <p style={{ fontSize: "14px", color: "var(--text-primary)" }}>{message.text}</p>
      </div>
    </div>
  );
}
