import type { ChatTextMessage, ChatMediaMessage } from "@/lib/types";

export function TextBubble({
  message,
  onQuickReply,
}: {
  message: ChatTextMessage;
  onQuickReply?: (value: string) => void;
}) {
  const isUser = message.role === "user";
  return (
    <div className={`chat-bubble-row ${isUser ? "user" : "assistant"}`}>
      <div>
        <div className={`chat-bubble ${isUser ? "user" : "assistant"}`}>{message.text}</div>
        {!isUser && message.quick_replies && message.quick_replies.length > 0 && (
          <div className="chat-quick-replies">
            {message.quick_replies.map((qr) => (
              <button
                key={qr}
                type="button"
                className="btn-secondary btn-sm"
                onClick={() => onQuickReply?.(qr)}
              >
                {qr}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// El backend no sirve los archivos subidos por HTTP (MEDIA_ROOT es interno,
// lo lee la Saga en el propio disco) — la preview solo existe mientras dura
// el `blob:` local que se crea al elegir el archivo (mismo criterio que
// campaigns/new/page.tsx). Tras recargar la página, se ve un marcador en vez
// de una imagen rota.
export function MediaBubble({
  message,
  previewUrl,
}: {
  message: ChatMediaMessage;
  previewUrl?: string;
}) {
  const isVideo = !!message.video_path;
  return (
    <div className={`chat-bubble-row ${message.role === "user" ? "user" : "assistant"}`}>
      <div className={`chat-bubble ${message.role === "user" ? "user" : "assistant"}`}>
        {previewUrl ? (
          isVideo ? (
            <video src={previewUrl} controls className="chat-media-preview" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={previewUrl} alt="Foto subida" className="chat-media-preview" />
          )
        ) : (
          <span>{isVideo ? "🎬 Video adjunto" : "📷 Foto adjunta"}</span>
        )}
      </div>
    </div>
  );
}
