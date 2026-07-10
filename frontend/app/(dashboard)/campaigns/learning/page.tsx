"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

// E-1 (auditoría): el backend ya registraba el aprendizaje de la IA en cada
// creación (GET /campaigns/learning-history) pero ninguna pantalla lo mostraba.
// Esta página es el timeline de ese entrenamiento: qué propuso, en qué modo
// estaba, con qué riesgo y — cuando exista el outcome loop (Fase B) — cómo le fue.

type Change = { change: string; why: string };
type LearningEntry = {
  id: string;
  product: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | string;
  status: string;
  params: Record<string, any> | null;
  analysis: {
    summary?: string;
    mode?: string;
    converged?: boolean;
    maturity_reason?: string;
    strengths?: string[];
    weaknesses?: string[];
    changes_proposed?: Change[];
    past_failures?: string[];
    evaluation_window_days?: number;
    confidence?: string;
  } | null;
  maturity: { converged?: boolean; mode?: string; reason?: string } | null;
  outcome: Record<string, any> | null;
  created_at: string | null;
};

const RISK_BADGE: Record<string, { cls: string; label: string }> = {
  LOW: { cls: "badge-success", label: "Riesgo bajo" },
  MEDIUM: { cls: "badge-warning", label: "Riesgo medio" },
  HIGH: { cls: "badge-danger", label: "Riesgo alto" },
};

function ModeBadge({ mode, converged }: { mode?: string; converged?: boolean }) {
  const optimized = converged || mode === "optimized";
  return (
    <span
      className={`badge ${optimized ? "badge-success" : "badge-warning"}`}
      title={optimized
        ? "La cuenta convergió a una configuración ganadora estable: la IA replica lo que funciona."
        : "La IA todavía está probando ajustes y dejándolos en evaluación."}
    >
      {optimized ? "🎯 Modo optimizado" : "🧪 Modo exploración"}
    </span>
  );
}

function OutcomeBlock({ outcome, windowDays }: { outcome: Record<string, any> | null; windowDays?: number }) {
  if (!outcome) {
    return (
      <div className="card-subtle" style={{ marginTop: "0.75rem" }}>
        <span style={{ fontSize: "13px", color: "var(--color-fog)" }}>
          ⏳ Resultado: en evaluación{windowDays ? ` (ventana de ${windowDays} días)` : ""}. La IA
          medirá el rendimiento real de esta campaña y lo registrará aquí.
        </span>
      </div>
    );
  }
  const verdict = String(outcome.verdict || "").toUpperCase();
  const badge = verdict === "WIN" ? "badge-success" : verdict === "LOSS" ? "badge-danger" : "badge-warning";
  const label = verdict === "WIN" ? "✓ Funcionó" : verdict === "LOSS" ? "✗ No funcionó" : "≈ Neutral";
  return (
    <div className="card-subtle" style={{ marginTop: "0.75rem" }}>
      <span className={`badge ${badge}`}>{label}</span>
      {outcome.cost_per_conv != null && (
        <span style={{ fontSize: "13px", color: "var(--color-mist)", marginLeft: 8 }}>
          S/.{Number(outcome.cost_per_conv).toFixed(2)} por conversación
          {outcome.expected_cost_per_conv != null && ` (esperado ~S/.${Number(outcome.expected_cost_per_conv).toFixed(2)})`}
        </span>
      )}
    </div>
  );
}

export default function LearningHistoryPage() {
  const [entries, setEntries] = useState<LearningEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getLearningHistory(50)
      .then((data: LearningEntry[]) => setEntries(data || []))
      .catch((e: any) => setError(e?.message || "No se pudo cargar el historial de aprendizaje."))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div style={{ color: "var(--text-secondary)" }}>Cargando historial de aprendizaje…</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Aprendizaje de la IA</h1>
          <p style={{ color: "var(--color-fog)", fontSize: "15px", marginTop: "4px", maxWidth: 640 }}>
            Cada vez que la IA crea una campaña, registra qué propuso y por qué. Este es su
            historial de entrenamiento para la cuenta activa: su razonamiento, su nivel de
            madurez y el resultado real de cada decisión.
          </p>
        </div>
        <Link href="/campaigns/new" className="btn-primary" style={{ whiteSpace: "nowrap" }}>
          + Crear Campaña
        </Link>
      </div>

      {error && (
        <div role="alert" className="card-subtle" style={{ color: "var(--color-coral-red)", fontSize: "14px" }}>
          {error}
        </div>
      )}

      {entries.length === 0 && !error ? (
        <div className="glass-card" style={{ textAlign: "center", padding: "3rem 1.5rem", display: "flex", flexDirection: "column", gap: "0.75rem", alignItems: "center" }}>
          <h3 style={{ color: "var(--color-paper)", fontSize: "18px", fontWeight: 590 }}>La IA todavía no tiene historial en esta cuenta</h3>
          <p style={{ color: "var(--text-secondary)", maxWidth: 460, lineHeight: 1.5 }}>
            El historial se construye con cada campaña creada desde la plataforma. Crea la primera
            y la IA registrará su análisis y aprenderá de los resultados.
          </p>
          <Link href="/campaigns/new" className="btn-primary" style={{ marginTop: "0.5rem" }}>Crear la primera campaña</Link>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
          {entries.map((e) => {
            const risk = RISK_BADGE[e.risk_level] || { cls: "badge-warning", label: `Riesgo ${e.risk_level}` };
            const a = e.analysis || {};
            return (
              <div key={e.id} className="glass-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.75rem", flexWrap: "wrap", marginBottom: "0.75rem" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                    <h3 style={{ fontSize: "16px", fontWeight: 590, color: "var(--color-paper)" }}>{e.product}</h3>
                    <ModeBadge mode={a.mode || e.maturity?.mode} converged={a.converged ?? e.maturity?.converged} />
                    <span className={`badge ${risk.cls}`}>{risk.label}</span>
                  </div>
                  <span className="mono" style={{ fontSize: "12px", color: "var(--color-fog)" }}>
                    {e.created_at ? new Date(e.created_at).toLocaleString() : ""}
                  </span>
                </div>

                {a.summary && (
                  <p style={{ fontSize: "14px", color: "var(--color-mist)", lineHeight: 1.6, marginBottom: "0.75rem" }}>
                    {a.summary}
                  </p>
                )}
                {(a.maturity_reason || e.maturity?.reason) && (
                  <p style={{ fontSize: "13px", color: "var(--color-fog)", lineHeight: 1.5, marginBottom: "0.75rem" }}>
                    {a.maturity_reason || e.maturity?.reason}
                  </p>
                )}

                {(a.changes_proposed?.length ?? 0) > 0 && (
                  <div style={{ marginBottom: "0.5rem" }}>
                    <h4 style={{ fontSize: "13px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "0.4rem" }}>
                      Qué decidió la IA y por qué
                    </h4>
                    <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: "6px" }}>
                      {a.changes_proposed!.map((c, i) => (
                        <li key={i} style={{ fontSize: "13px", color: "var(--text-secondary)", lineHeight: 1.5, paddingLeft: "14px", position: "relative" }}>
                          <span style={{ position: "absolute", left: 0, color: "var(--color-signal-teal)" }}>→</span>
                          <strong style={{ color: "var(--color-mist)", fontWeight: 510 }}>{c.change}.</strong> {c.why}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {(a.past_failures?.length ?? 0) > 0 && (
                  <details style={{ marginTop: "0.5rem" }}>
                    <summary style={{ fontSize: "13px", color: "var(--color-fog)", cursor: "pointer" }}>
                      Errores del pasado que evitó ({a.past_failures!.length})
                    </summary>
                    <ul style={{ listStyle: "none", marginTop: "0.4rem", display: "flex", flexDirection: "column", gap: "4px" }}>
                      {a.past_failures!.map((f, i) => (
                        <li key={i} style={{ fontSize: "13px", color: "var(--text-secondary)", paddingLeft: "14px", position: "relative" }}>
                          <span style={{ position: "absolute", left: 0, color: "var(--color-coral-red)" }}>✗</span>{f}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}

                <OutcomeBlock outcome={e.outcome} windowDays={a.evaluation_window_days} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
