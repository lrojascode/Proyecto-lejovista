"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AdminOrganization } from "@/lib/types";

/** Panel de plataforma: alta de clientes y módulos.
 *
 * Lo que esta pantalla NO hace, y es a propósito: no muestra campañas, gastos
 * ni conversaciones de ningún cliente. El rol de plataforma administra
 * organizaciones; no abre sus datos. Ese límite es lo que mantiene en pie el
 * aislamiento por organización.
 *
 * No está en el menú: se llega escribiendo /admin. El backend responde 403 a
 * quien no tenga el flag, así que ocultarla del menú es comodidad, no
 * seguridad.
 */
export default function AdminPanel() {
  const [orgs, setOrgs] = useState<AdminOrganization[]>([]);
  const [catalogo, setCatalogo] = useState<Record<string, string>>({});
  const [noApagables, setNoApagables] = useState<string[]>([]);
  const [cargando, setCargando] = useState(true);
  const [sinPermiso, setSinPermiso] = useState(false);
  const [msg, setMsg] = useState("");
  const [creando, setCreando] = useState(false);
  // Enlace de la ultima invitacion creada. Se muestra siempre que el correo no
  // haya salido de verdad: `send_email` es best-effort y sin BREVO_API_KEY
  // registra el envio y devuelve exito sin mandar nada.
  const [enlaceInvitacion, setEnlaceInvitacion] = useState<string | null>(null);
  const [form, setForm] = useState({ slug: "", name: "", admin_email: "", accent_color: "#02b8cc" });

  const cargar = async () => {
    try {
      const [lista, mods] = await Promise.all([api.adminListOrganizations(), api.adminListModules()]);
      setOrgs(lista);
      setCatalogo(mods.available);
      setNoApagables(mods.undisableable);
      setSinPermiso(false);
    } catch {
      // El backend responde 403 a quien no es admin de plataforma. Se muestra
      // un mensaje claro en vez de una pantalla vacía sin explicación.
      setSinPermiso(true);
    } finally {
      setCargando(false);
    }
  };

  useEffect(() => { cargar(); }, []);

  const alternarModulo = async (org: AdminOrganization, clave: string) => {
    if (noApagables.includes(clave)) return;
    const actuales = new Set(org.enabled_modules);
    if (actuales.has(clave)) actuales.delete(clave); else actuales.add(clave);
    try {
      const actualizada = await api.adminUpdateModules(org.id, [...actuales]);
      setOrgs((prev) => prev.map((o) => (o.id === org.id ? actualizada : o)));
      setMsg(`Módulos de ${org.name} actualizados.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "No se pudo actualizar.");
    }
  };

  const crear = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreando(true);
    setMsg("");
    try {
      const creada = await api.adminCreateOrganization({
        slug: form.slug.trim().toLowerCase(),
        name: form.name.trim(),
        accent_color: form.accent_color,
        admin_email: form.admin_email.trim() || undefined,
      });
      const inv = creada.invitacion;
      setForm({ slug: "", name: "", admin_email: "", accent_color: "#02b8cc" });
      if (!inv) {
        setEnlaceInvitacion(null);
        setMsg("Cliente creado. Todavía no tiene ningún usuario: invitá a su primer ADMIN.");
      } else if (inv.correo_enviado) {
        setEnlaceInvitacion(null);
        setMsg(`Cliente creado. Se le envió la invitación a ${inv.email} para que elija su contraseña.`);
      } else {
        // El correo NO salió. Decirlo, y dar el enlace: es lo único que
        // permite terminar el alta hoy.
        setEnlaceInvitacion(inv.invite_url);
        setMsg(`Cliente creado, pero el correo no se envió (falta configurar el envío). Pasale este enlace a ${inv.email}:`);
      }
      await cargar();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "No se pudo crear el cliente.");
    } finally {
      setCreando(false);
    }
  };

  if (cargando) return <div style={{ color: "var(--color-fog)" }}>Cargando panel...</div>;

  if (sinPermiso) {
    return (
      <div className="glass-card" style={{ padding: "24px" }}>
        <h2 style={{ fontSize: "20px", fontWeight: 590 }}>Sección de plataforma</h2>
        <p style={{ color: "var(--color-fog)", marginTop: "8px" }}>
          Esta pantalla es solo para administradores de la plataforma. Si creés que deberías tener
          acceso, pedí que te habiliten el permiso.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: "24px" }}>
        <h2 style={{ fontSize: "24px", fontWeight: 590, letterSpacing: "-0.012em" }}>Clientes</h2>
        <p style={{ color: "var(--color-fog)", fontSize: "15px", marginTop: "4px" }}>
          Alta de clientes y qué módulos ve cada uno. Desde acá no se ven los datos de sus cuentas.
        </p>
        {msg ? (
          <p className="mono" style={{ color: "var(--color-accent)", fontSize: "13px", marginTop: "8px" }}>{msg}</p>
        ) : null}
        {enlaceInvitacion ? (
          <div style={{ marginTop: "8px", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
            <code
              style={{
                background: "var(--color-carbon)", padding: "6px 10px", borderRadius: "6px",
                fontSize: "12px", wordBreak: "break-all", color: "var(--color-mist)",
              }}
            >
              {enlaceInvitacion}
            </code>
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={() => navigator.clipboard?.writeText(enlaceInvitacion)}
            >
              Copiar
            </button>
            <span style={{ color: "var(--color-ash)", fontSize: "12px" }}>
              Vence en 7 días y se usa una sola vez.
            </span>
          </div>
        ) : null}
      </div>

      <form onSubmit={crear} className="glass-card" style={{ padding: "20px", marginBottom: "24px" }}>
        <h3 style={{ fontSize: "16px", fontWeight: 510, marginBottom: "12px" }}>Nuevo cliente</h3>
        <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "13px", color: "var(--color-fog)" }}>
            Subdominio
            <input
              className="form-input" required value={form.slug} placeholder="cliente-nuevo"
              pattern="^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"
              title="Letras minúsculas, números y guiones. Es el subdominio y no se puede cambiar después."
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
              style={{ minWidth: "180px" }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "13px", color: "var(--color-fog)" }}>
            Nombre
            <input
              className="form-input" required value={form.name} placeholder="Cliente Nuevo SAC"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              style={{ minWidth: "200px" }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "13px", color: "var(--color-fog)" }}>
            Email del primer ADMIN
            <input
              className="form-input" type="email" value={form.admin_email} placeholder="dueno@cliente.com"
              onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
              style={{ minWidth: "220px" }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "13px", color: "var(--color-fog)" }}>
            Acento
            <input
              type="color" value={form.accent_color}
              onChange={(e) => setForm({ ...form, accent_color: e.target.value })}
              style={{ width: "40px", height: "36px", padding: 0, border: "none", background: "transparent", cursor: "pointer" }}
            />
          </label>
          <button type="submit" className="btn-primary" disabled={creando}>
            {creando ? "Creando..." : "Crear cliente"}
          </button>
        </div>
        <p style={{ color: "var(--color-ash)", fontSize: "12px", marginTop: "10px" }}>
          Nadie escribe la contraseña del cliente: se le manda una invitación de un solo uso y él elige la suya.
        </p>
      </form>

      <div className="glass-card" style={{ padding: "8px 0" }}>
        {orgs.length === 0 ? (
          <p style={{ color: "var(--color-fog)", textAlign: "center", padding: "24px" }}>
            Todavía no hay clientes dados de alta.
          </p>
        ) : (
          orgs.map((org) => (
            <div key={org.id} style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-color)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "16px", flexWrap: "wrap", alignItems: "baseline" }}>
                <div>
                  <strong style={{ fontSize: "15px", color: "var(--color-paper)" }}>
                    {org.display_name || org.name}
                  </strong>
                  <span className="mono" style={{ color: "var(--color-ash)", fontSize: "12px", marginLeft: "8px" }}>
                    {org.slug}
                  </span>
                </div>
                <span className="mono" style={{ color: "var(--color-fog)", fontSize: "12px" }}>
                  {org.miembros} {org.miembros === 1 ? "usuario" : "usuarios"} ·{" "}
                  {org.cuentas_publicitarias} {org.cuentas_publicitarias === 1 ? "cuenta" : "cuentas"} · {org.status}
                </span>
              </div>
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "12px" }}>
                {Object.entries(catalogo).map(([clave, etiqueta]) => {
                  const activo = org.enabled_modules.includes(clave);
                  const fijo = noApagables.includes(clave);
                  return (
                    <button
                      key={clave}
                      type="button"
                      onClick={() => alternarModulo(org, clave)}
                      disabled={fijo}
                      title={fijo ? "Configuración no se puede apagar: sin ella el cliente no puede conectar Meta ni administrarse." : etiqueta}
                      className={activo ? "badge badge-success" : "badge"}
                      style={{
                        cursor: fijo ? "default" : "pointer",
                        opacity: activo ? 1 : 0.45,
                        border: "1px solid var(--border-color)",
                        background: "transparent",
                        font: "inherit",
                        fontSize: "12px",
                      }}
                    >
                      {activo ? "✓" : "○"} {etiqueta}
                    </button>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
