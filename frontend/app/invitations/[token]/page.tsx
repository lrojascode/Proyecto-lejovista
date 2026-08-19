"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { InvitationDetails } from "@/lib/types";

export default function AcceptInvitationPage() {
  const router = useRouter();
  const params = useParams();
  const token = typeof params?.token === "string" ? params.token : "";

  const [details, setDetails] = useState<InvitationDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (!token) return;
    api
      .getInvitationDetails(token)
      .then((data) => {
        setDetails(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message || "No se pudo cargar la información de la invitación.");
        setLoading(false);
      });
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");

    if (!fullName.trim()) {
      setFormError("Ingresa tu nombre completo.");
      return;
    }
    if (password.length < 8) {
      setFormError("La contraseña debe tener al menos 8 caracteres.");
      return;
    }
    if (password !== confirmPassword) {
      setFormError("Las contraseñas no coinciden.");
      return;
    }

    setSubmitting(true);
    try {
      await api.acceptInvitation(token, { full_name: fullName.trim(), password });
      router.push("/");
    } catch (err: any) {
      setFormError(err.message || "Error al aceptar la invitación.");
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <p style={{ color: "var(--text-secondary)" }}>Cargando invitación...</p>
      </div>
    );
  }

  if (error || !details) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
        <div className="glass-card" style={{ maxWidth: "460px", width: "100%", textAlign: "center" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 590, marginBottom: "8px", color: "var(--color-coral-red)" }}>
            Invitación no disponible
          </h2>
          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginBottom: "20px" }}>
            {error || "El enlace de invitación no es válido o ya no existe."}
          </p>
          <Link href="/login" className="btn-primary">
            Ir al inicio de sesión
          </Link>
        </div>
      </div>
    );
  }

  if (details.is_accepted) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
        <div className="glass-card" style={{ maxWidth: "460px", width: "100%", textAlign: "center" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 590, marginBottom: "8px" }}>
            Invitación ya aceptada
          </h2>
          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginBottom: "20px" }}>
            Esta invitación ya fue utilizada previamente. Inicia sesión con tus credenciales.
          </p>
          <Link href="/login" className="btn-primary">
            Iniciar Sesión
          </Link>
        </div>
      </div>
    );
  }

  if (details.is_expired) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
        <div className="glass-card" style={{ maxWidth: "460px", width: "100%", textAlign: "center" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 590, marginBottom: "8px", color: "var(--color-amber)" }}>
            Invitación expirada
          </h2>
          <p style={{ color: "var(--text-secondary)", fontSize: "14px", marginBottom: "20px" }}>
            El enlace de invitación ha caducado. Solicita al administrador de <strong>{details.organization_name}</strong> que te envíe una nueva invitación.
          </p>
          <Link href="/login" className="btn-secondary">
            Volver al inicio
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
      <div className="glass-card" style={{ maxWidth: "480px", width: "100%" }}>
        <div style={{ marginBottom: "24px", textAlign: "center" }}>
          <h1 style={{ fontSize: "22px", fontWeight: 590, letterSpacing: "-0.012em", marginBottom: "6px" }}>
            Únete a {details.organization_name}
          </h1>
          <p style={{ color: "var(--color-fog)", fontSize: "14px" }}>
            Has sido invitado a formar parte del equipo como{" "}
            <span style={{ fontWeight: 600, color: "var(--color-paper)" }}>{details.role}</span>.
          </p>
        </div>

        {formError && (
          <div
            style={{
              padding: "10px 14px",
              background: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              borderRadius: "6px",
              color: "var(--color-coral-red)",
              fontSize: "13px",
              marginBottom: "16px",
            }}
          >
            {formError}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div>
            <label className="form-label">Correo electrónico</label>
            <input
              type="email"
              className="form-input"
              value={details.email}
              disabled
              style={{ background: "rgba(255, 255, 255, 0.03)", opacity: 0.8 }}
            />
          </div>

          <div>
            <label className="form-label">Tu nombre completo</label>
            <input
              type="text"
              className="form-input"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Ej. Juan Pérez"
              required
            />
          </div>

          <div>
            <label className="form-label">Contraseña</label>
            <input
              type="password"
              className="form-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Mínimo 8 caracteres"
              required
              minLength={8}
            />
          </div>

          <div>
            <label className="form-label">Confirmar contraseña</label>
            <input
              type="password"
              className="form-input"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Repite tu contraseña"
              required
              minLength={8}
            />
          </div>

          <button
            type="submit"
            className="btn-primary"
            disabled={submitting}
            style={{ marginTop: "8px", width: "100%", justifyContent: "center" }}
          >
            {submitting ? "Creando tu cuenta..." : "Aceptar Invitación y Entrar"}
          </button>
        </form>
      </div>
    </div>
  );
}
