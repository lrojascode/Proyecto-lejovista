import type { ChatMessage } from "@/lib/types";
import { TextBubble, MediaBubble } from "./ChatBubble";
import { ReviewCard } from "./ReviewCard";
import { RiskCard } from "./RiskCard";
import { ProgressCard } from "./ProgressCard";
import { ResultCard } from "./ResultCard";
import { ErrorCard } from "./ErrorCard";

export function MessageList({
  messages,
  mediaPreviewUrls,
  onDecision,
  onQuickReply,
  onErrorAction,
  onCreateAnother,
  externalAccountId,
  decisionsDisabled,
}: {
  messages: ChatMessage[];
  mediaPreviewUrls: Record<string, string>;
  onDecision: (cardId: string, decision: "accept" | "reject" | "change", text?: string) => void;
  onQuickReply: (value: string) => void;
  onErrorAction: (action: "accept" | "reject" | "change") => void;
  onCreateAnother: () => void;
  externalAccountId?: string;
  decisionsDisabled?: boolean;
}) {
  return (
    <>
      {messages.map((m) => {
        switch (m.type) {
          case "text":
            return <TextBubble key={m.id} message={m} onQuickReply={onQuickReply} />;
          case "media": {
            const path = m.image_path || m.video_path || "";
            return <MediaBubble key={m.id} message={m} previewUrl={mediaPreviewUrls[path]} />;
          }
          case "review_card":
            return (
              <ReviewCard
                key={m.id}
                card={m}
                previewUrl={mediaPreviewUrls[m.media_preview?.image_path || m.media_preview?.video_path || ""]}
                disabled={decisionsDisabled}
                onDecision={(decision, text) => onDecision(m.card_id, decision, text)}
              />
            );
          case "risk_card":
            return (
              <RiskCard
                key={m.id}
                card={m}
                disabled={decisionsDisabled}
                onDecision={(decision, text) => onDecision(m.card_id, decision, text)}
              />
            );
          case "progress":
            return <ProgressCard key={m.id} message={m} />;
          case "result":
            return (
              <ResultCard
                key={m.id}
                message={m}
                externalAccountId={externalAccountId}
                onCreateAnother={onCreateAnother}
              />
            );
          case "error":
            return <ErrorCard key={m.id} message={m} onAction={onErrorAction} />;
          default:
            return null;
        }
      })}
    </>
  );
}
