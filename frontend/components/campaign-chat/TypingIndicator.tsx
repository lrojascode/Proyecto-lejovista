// Tres puntos animados mientras el servidor procesa el turno. Sin esto, entre
// que enviás y llega la respuesta no pasa nada en pantalla durante segundos y
// parece que el chat se colgó.
export function TypingIndicator() {
  return (
    <div className="chat-bubble-row assistant" aria-live="polite" aria-label="Escribiendo">
      <div className="chat-bubble assistant chat-typing">
        <span className="chat-typing-dot" />
        <span className="chat-typing-dot" />
        <span className="chat-typing-dot" />
      </div>
    </div>
  );
}
