import { useRef, useState } from "react";
import { SpinnerIcon } from "@/components/icons";

export function Composer({
  onSendText,
  onSendFile,
  disabled,
  uploading,
  placeholder = "Escribí tu mensaje...",
}: {
  onSendText: (text: string) => void;
  onSendFile: (file: File) => void;
  disabled?: boolean;
  uploading?: boolean;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSendText(trimmed);
    setText("");
  };

  return (
    <div className="chat-composer">
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onSendFile(file);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className="btn-secondary chat-file-btn"
        disabled={disabled || uploading}
        onClick={() => fileInputRef.current?.click()}
        aria-label="Subir foto o video"
      >
        {uploading ? <SpinnerIcon size={16} /> : "📎"}
      </button>
      <textarea
        className="form-input"
        placeholder={placeholder}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        rows={1}
      />
      <button type="button" className="btn-primary" disabled={disabled || !text.trim()} onClick={submit}>
        Enviar
      </button>
    </div>
  );
}
