"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAdAccount } from "@/components/providers/AdAccountProvider";
import { useWebSocket } from "@/components/providers/WebSocketProvider";
import type { ChatSessionResponse } from "@/lib/types";
import { MessageList } from "@/components/campaign-chat/MessageList";
import { Composer } from "@/components/campaign-chat/Composer";

const SESSION_STORAGE_KEY = "campaign_chat_session_id";
const POLL_INTERVAL_MS = 2500;
// La Saga tarda ~3 min (4 pasos con countdown=40s entre sí, ver
// campaigns/new/page.tsx) — mismo margen de ~5 min que el formulario.
const POLL_MAX_ATTEMPTS = 120;

function newTurnId(): string {
  return crypto.randomUUID();
}

export default function CampaignChatPage() {
  const { activeAdAccountId, accounts, loading: accountsLoading } = useAdAccount();
  const { lastEvent } = useWebSocket();

  const [session, setSession] = useState<ChatSessionResponse | null>(null);
  const [guardError, setGuardError] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [mediaPreviewUrls, setMediaPreviewUrls] = useState<Record<string, string>>({});

  const transcriptRef = useRef<HTMLDivElement>(null);
  const pollAttemptsRef = useRef(0);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const activeAccount = accounts.find((a) => a.id === activeAdAccountId) || null;

  const startSession = async () => {
    setInitializing(true);
    setGuardError(null);
    try {
      const existingId = sessionStorage.getItem(SESSION_STORAGE_KEY);
      if (existingId) {
        try {
          const existing = await api.getCampaignChatSession(existingId);
          if (existing.status === "OPEN") {
            setSession(existing);
            setInitializing(false);
            return;
          }
        } catch {
          // La sesión guardada ya no existe o es de otra cuenta — se descarta.
        }
        sessionStorage.removeItem(SESSION_STORAGE_KEY);
      }
      const created = await api.startCampaignChat(activeAdAccountId || undefined);
      sessionStorage.setItem(SESSION_STORAGE_KEY, created.id);
      setSession(created);
    } catch (e: any) {
      setGuardError(e.message || "No se pudo iniciar el chat.");
    } finally {
      setInitializing(false);
    }
  };

  useEffect(() => {
    if (accountsLoading) return;
    startSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountsLoading, activeAdAccountId]);

  useEffect(() => {
    const el = transcriptRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [session?.messages.length]);

  // Polling durante la creación (~3 min), cortocircuitado por el WS.
  useEffect(() => {
    if (!session || session.state !== "CREATING" || session.status !== "OPEN") return;

    pollAttemptsRef.current = 0;
    const poll = async () => {
      pollAttemptsRef.current += 1;
      try {
        const updated = await api.advanceCampaignChat(session.id);
        setSession(updated);
        if (updated.status === "OPEN" && pollAttemptsRef.current < POLL_MAX_ATTEMPTS) {
          pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch {
        if (pollAttemptsRef.current < POLL_MAX_ATTEMPTS) {
          pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      }
    };
    pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.state, session?.status]);

  useEffect(() => {
    if (!session || session.state !== "CREATING") return;
    if (!lastEvent || lastEvent.event_type !== "campaign_creation") return;
    if (lastEvent.job_id !== session.draft_id) return;
    api.advanceCampaignChat(session.id).then(setSession).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent]);

  const isGate = session ? ["REVIEW_PLAN", "CONFIRM_RISK"].includes(session.state) : false;
  const isClosed = session ? session.status !== "OPEN" : false;
  const inputDisabled = busy || uploading || isClosed || session?.state === "CREATING";

  const handleSendText = async (text: string) => {
    if (!session) return;
    setBusy(true);
    setErrorMessage("");
    try {
      const updated = await api.sendCampaignChatMessage(session.id, { clientTurnId: newTurnId(), text });
      setSession(updated);
    } catch (e: any) {
      setErrorMessage(e.message || "No se pudo enviar el mensaje.");
    } finally {
      setBusy(false);
    }
  };

  const handleSendFile = async (file: File) => {
    if (!session) return;
    setUploading(true);
    setErrorMessage("");
    const isVideo = file.type.startsWith("video/");
    const previewUrl = URL.createObjectURL(file);
    try {
      const uploaded = isVideo
        ? await api.uploadCampaignVideo(file)
        : await api.uploadCampaignImage(file);
      const path = isVideo ? (uploaded as any).video_path : (uploaded as any).image_path;
      setMediaPreviewUrls((prev) => ({ ...prev, [path]: previewUrl }));
      const updated = await api.sendCampaignChatMessage(session.id, {
        clientTurnId: newTurnId(),
        imagePath: isVideo ? undefined : path,
        videoPath: isVideo ? path : undefined,
      });
      setSession(updated);
    } catch (e: any) {
      URL.revokeObjectURL(previewUrl);
      setErrorMessage(e.message || "No se pudo subir el archivo.");
    } finally {
      setUploading(false);
    }
  };

  const handleDecision = async (cardId: string, decision: "accept" | "reject" | "change", text?: string) => {
    if (!session) return;
    setBusy(true);
    setErrorMessage("");
    try {
      const updated = await api.decideCampaignChatCard(session.id, {
        clientTurnId: newTurnId(), cardId, decision, text,
      });
      setSession(updated);
    } catch (e: any) {
      setErrorMessage(e.message || "No se pudo procesar la decisión.");
    } finally {
      setBusy(false);
    }
  };

  const handleErrorAction = (action: "accept" | "reject" | "change") => {
    if (!session) return;
    const lastCard = [...session.messages].reverse().find((m) => m.type === "review_card" || m.type === "risk_card");
    if (lastCard && "card_id" in lastCard) {
      handleDecision((lastCard as any).card_id, action);
    }
  };

  const handleCreateAnother = () => {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
    setSession(null);
    setMediaPreviewUrls({});
    startSession();
  };

  if (accountsLoading || initializing) {
    return <div style={{ color: "var(--text-secondary)" }}>Preparando el chat...</div>;
  }

  if (guardError) {
    return (
      <div className="glass-card" style={{ maxWidth: "560px", margin: "48px auto" }}>
        <h2 style={{ fontSize: "18px", fontWeight: 590, marginBottom: "8px" }}>No podemos empezar todavía</h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginBottom: "16px" }}>{guardError}</p>
        <div style={{ display: "flex", gap: "8px" }}>
          <Link href="/settings" className="btn-primary">Ir a Configuración</Link>
          <Link href="/campaigns" className="btn-secondary">Volver a Campañas</Link>
        </div>
      </div>
    );
  }

  if (!session) return null;

  return (
    <div>
      <div style={{ marginBottom: "16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Chat de campañas</h2>
          <p style={{ color: "var(--color-fog)", fontSize: "15px", marginTop: "4px" }}>
            Contame qué querés promocionar y armamos la campaña juntos.
          </p>
        </div>
        <Link href="/campaigns/new" className="btn-secondary">Usar el formulario</Link>
      </div>

      <div className="glass-card chat-shell">
        <div className="chat-transcript" ref={transcriptRef}>
          <MessageList
            messages={session.messages}
            mediaPreviewUrls={mediaPreviewUrls}
            onDecision={handleDecision}
            onQuickReply={handleSendText}
            onErrorAction={handleErrorAction}
            onCreateAnother={handleCreateAnother}
            externalAccountId={activeAccount?.external_account_id}
            decisionsDisabled={busy || isClosed}
          />
        </div>

        {errorMessage && (
          <p style={{ color: "var(--color-coral-red)", fontSize: "13px", padding: "0 8px" }}>{errorMessage}</p>
        )}

        {!isClosed && (
          <Composer
            onSendText={handleSendText}
            onSendFile={handleSendFile}
            disabled={inputDisabled}
            uploading={uploading}
            placeholder={isGate ? "¿Algo que cambiar? Escribilo acá..." : "Escribí tu mensaje..."}
          />
        )}

        {isClosed && session.status === "CLOSED_CANCELLED" && (
          <div style={{ padding: "16px 8px", textAlign: "center" }}>
            <button type="button" className="btn-primary" onClick={handleCreateAnother}>
              Empezar una campaña nueva
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
