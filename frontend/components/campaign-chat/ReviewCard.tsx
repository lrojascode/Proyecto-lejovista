import { useState } from "react";
import type { ChatReviewCard } from "@/lib/types";

export function ReviewCard({
  card,
  previewUrl,
  onDecision,
  disabled,
}: {
  card: ChatReviewCard;
  previewUrl?: string;
  onDecision: (decision: "accept" | "reject" | "change", text?: string) => void;
  disabled?: boolean;
}) {
  const [changing, setChanging] = useState(false);
  const [changeText, setChangeText] = useState("");

  const isVideo = !!card.media_preview?.video_path;

  return (
    <div className="chat-bubble-row assistant">
      <div className="glass-card chat-card">
        <h4 style={{ fontSize: "15px", fontWeight: 590, marginBottom: "12px" }}>{card.title}</h4>

        {previewUrl && (
          isVideo ? (
            <video src={previewUrl} controls className="chat-media-preview" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={previewUrl} alt="Foto de la campaña" className="chat-media-preview" />
          )
        )}

        {card.fields.map((f) => (
          <div key={f.key} className="chat-card-field">
            <span className="chat-card-field-label">{f.label}</span>
            <span className="chat-card-field-value">{f.value}</span>
          </div>
        ))}

        {card.copy_preview?.primary_text?.[0] && (
          <div style={{ marginTop: "12px" }}>
            <p className="chat-card-field-label" style={{ marginBottom: "4px" }}>Texto del anuncio</p>
            <p style={{ fontSize: "14px", color: "var(--color-mist)", whiteSpace: "pre-wrap" }}>
              {card.copy_preview.primary_text[0]}
            </p>
          </div>
        )}

        {(card.evidence ?? []).length > 0 && (
          <ul style={{ marginTop: "12px", paddingLeft: "18px", fontSize: "13px", color: "var(--text-secondary)" }}>
            {(card.evidence ?? []).map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        )}

        {changing ? (
          <div style={{ marginTop: "16px", display: "flex", gap: "8px" }}>
            <input
              className="form-input"
              placeholder="¿Qué querés cambiar? Ej: poné 300 soles"
              value={changeText}
              onChange={(e) => setChangeText(e.target.value)}
              disabled={disabled}
              onKeyDown={(e) => {
                if (e.key === "Enter" && changeText.trim()) {
                  onDecision("change", changeText.trim());
                  setChangeText("");
                  setChanging(false);
                }
              }}
            />
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={disabled || !changeText.trim()}
              onClick={() => {
                onDecision("change", changeText.trim());
                setChangeText("");
                setChanging(false);
              }}
            >
              Enviar
            </button>
          </div>
        ) : (
          <div className="chat-card-actions">
            {card.actions.map((a) => (
              <button
                key={a.action}
                type="button"
                disabled={disabled}
                className={a.style === "primary" ? "btn-primary" : a.style === "danger" ? "btn-danger" : "btn-secondary"}
                onClick={() => (a.action === "change" ? setChanging(true) : onDecision(a.action))}
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
