"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";

/** Elegir la contraseña nueva desde el enlace del correo.
 *
 * Las validaciones de acá (mínimo 8, confirmación) son de comodidad: el mínimo
 * lo exige también el schema del backend. La confirmación no — es solo para que
 * un typo no deje a la persona afuera de nuevo, que es justo de donde viene.
 */
export default function ResetPasswordPage() {
  const params = useParams<{ token: string }>();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirmacion, setConfirmacion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [listo, setListo] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (password.length < 8) {
      setError("La contraseña tiene que tener al menos 8 caracteres.");
      return;
    }
    if (password !== confirmacion) {
      setError("Las dos contraseñas no coinciden.");
      return;
    }
    setLoading(true);
    try {
      await api.resetPassword(params.token, password);
      setListo(true);
      setTimeout(() => router.push("/login"), 2500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar la contraseña.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-card" style={{ maxWidth: "420px", margin: "4rem auto", padding: "2rem" }}>
      <h1 style={{ fontSize: "20px", fontWeight: 590, marginBottom: "1rem" }}>
        Elegí tu contraseña nueva
      </h1>

      {listo ? (
        <p style={{ color: "var(--success)", fontSize: "0.9rem" }}>
          Listo. Te llevamos a iniciar sesión...
        </p>
      ) : (
        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <input
            type="password"
            className="form-input"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Contraseña nueva (mínimo 8)"
            aria-label="Contraseña nueva"
          />
          <input
            type="password"
            className="form-input"
            required
            value={confirmacion}
            onChange={(e) => setConfirmacion(e.target.value)}
            placeholder="Repetila"
            aria-label="Repetir contraseña"
          />
          {error && (
            <div style={{ color: "var(--danger)", fontSize: "0.85rem" }}>
              {error}
              {error.includes("no sirve") && (
                <>
                  {" "}
                  <a href="/forgot-password" style={{ color: "var(--color-signal-teal)" }}>
                    Pedir uno nuevo
                  </a>
                </>
              )}
            </div>
          )}
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? "Guardando..." : "Guardar contraseña"}
          </button>
        </form>
      )}
    </div>
  );
}
