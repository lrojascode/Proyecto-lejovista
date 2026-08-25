"use client";

import { useState } from "react";
import { api } from "@/lib/api";

/** Pedir el enlace para elegir una contraseña nueva.
 *
 * La respuesta es SIEMPRE la misma exista o no el correo — el backend lo hace a
 * propósito y la pantalla no lo puede contradecir: si acá dijéramos "ese correo
 * no existe", el endpoint quedaría convertido en un verificador de qué correos
 * tienen cuenta.
 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api.forgotPassword(email.trim());
      setEnviado(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo enviar el enlace.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-card" style={{ maxWidth: "420px", margin: "4rem auto", padding: "2rem" }}>
      <h1 style={{ fontSize: "20px", fontWeight: 590, marginBottom: "0.5rem" }}>
        Recuperar contraseña
      </h1>

      {enviado ? (
        <>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem", lineHeight: 1.5 }}>
            Si ese correo tiene una cuenta, te llega un enlace para elegir una
            contraseña nueva. Revisá también el spam.
          </p>
          <p style={{ color: "var(--text-muted)", fontSize: "0.8rem", marginTop: "1rem" }}>
            El enlace vale 30 minutos y una sola vez.
          </p>
          <a href="/login" style={{ color: "var(--color-signal-teal)", fontSize: "0.9rem" }}>
            Volver a iniciar sesión
          </a>
        </>
      ) : (
        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem", margin: 0 }}>
            Poné tu correo y te mandamos un enlace para elegir una contraseña nueva.
          </p>
          <input
            type="email"
            className="form-input"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="correo@empresa.com"
            aria-label="Correo electrónico"
          />
          {error && (
            <div style={{ color: "var(--danger)", fontSize: "0.85rem" }}>{error}</div>
          )}
          <button type="submit" className="btn-primary" disabled={loading || !email.trim()}>
            {loading ? "Enviando..." : "Enviar enlace"}
          </button>
          <a href="/login" style={{ color: "var(--color-signal-teal)", fontSize: "0.85rem", textAlign: "center" }}>
            Volver
          </a>
        </form>
      )}
    </div>
  );
}
