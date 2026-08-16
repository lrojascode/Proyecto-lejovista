import { useRef } from "react";
import { SpinnerIcon } from "@/components/icons";

// El texto lo maneja la página, no este componente: si el envío falla hay que
// poder devolvérselo al usuario, y con el estado acá adentro se perdía.
export function Composer({
  value,
  onChange,
  onSubmit,
  onSendFile,
  disabled,
  uploading,
  placeholder = "Escribí tu mensaje...",
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onSendFile: (file: File) => void;
  disabled?: boolean;
  uploading?: boolean;
  placeholder?: string;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);

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
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!disabled) onSubmit();
          }
        }}
        rows={1}
      />
      <button type="button" className="btn-primary" disabled={disabled || !value.trim()} onClick={onSubmit}>
        Enviar
      </button>
    </div>
  );
}
