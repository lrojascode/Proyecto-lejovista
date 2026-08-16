import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
        {/* El asistente escribe markdown (**negrita** para el dato clave,
            viñetas para listar campañas). Sin renderizarlo, el nombre de una
            campaña salía en pantalla como **ALT_MSJ_LEADS_JUN290**. El texto
            del usuario NO se interpreta: es entrada suya, se muestra tal cual. */}
        <div className={`chat-bubble ${isUser ? "user" : "assistant"}`}>
          {isUser ? message.text : (
            <div className="chat-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
            </div>
          )}
        </div>
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
