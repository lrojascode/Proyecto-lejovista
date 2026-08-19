"use client";

import Link from "next/link";

export default function DataDeletionInstructions() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", background: "var(--color-obsidian)" }}>
      {/* Header */}
      <header style={{
        width: "100%",
        maxWidth: "960px",
        margin: "0 auto",
        padding: "20px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottom: "1px solid var(--border-subtle)",
      }}>
        <Link href="/landing" style={{ fontSize: "18px", fontWeight: 590, color: "var(--color-paper)", textDecoration: "none" }}>
          LejoVista
        </Link>
        <Link href="/login" className="btn-secondary btn-sm" style={{ textDecoration: "none" }}>
          Iniciar Sesión
        </Link>
      </header>

      {/* Content */}
      <main style={{
        flexGrow: 1,
        maxWidth: "960px",
        width: "100%",
        margin: "0 auto",
        padding: "48px 24px 80px 24px",
        color: "var(--color-fog)",
        lineHeight: 1.7,
      }}>
        <h1 style={{ fontSize: "32px", fontWeight: 590, color: "var(--color-paper)", marginBottom: "8px" }}>
          Instrucciones de Eliminación de Datos de Usuario (User Data Deletion)
        </h1>
        <p style={{ fontSize: "14px", color: "var(--color-ash)", marginBottom: "32px" }}>
          Requerimiento de Meta Platform y Cumplimiento de Privacidad GDPR / LGPD
        </p>

        <section style={{ marginBottom: "28px" }}>
          <p>
            De acuerdo con las Políticas de la Plataforma Meta y los estándares de privacidad de datos, en LejoVista
            proporcionamos un mecanismo claro y directo para que los usuarios puedan solicitar la eliminación total de sus datos personales
            y de sus cuentas publicitarias conectadas.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            Opción 1: Eliminación Automática desde el Panel de Configuración
          </h2>
          <ol style={{ paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "8px" }}>
            <li>Inicia sesión en tu cuenta de LejoVista.</li>
            <li>Dirígete a la sección <strong>Configuración</strong> (`/settings`).</li>
            <li>En la tarjeta <strong>Cuentas Publicitarias Conectadas</strong>, haz clic en el botón <strong>Archivar</strong> de la cuenta que deseas desconectar.</li>
            <li>Haz clic en <strong>Ver Cuentas Archivadas</strong> y presiona <strong>Eliminar Definitivamente</strong>.</li>
            <li>Confirma la acción escribiendo el nombre de la cuenta. El sistema eliminará inmediatamente las credenciales cifradas, tokens de acceso y métricas sincronizadas.</li>
          </ol>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            Opción 2: Desvinculación desde tu cuenta de Facebook / Meta Business
          </h2>
          <ol style={{ paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "8px" }}>
            <li>Ingresa a tu cuenta de Facebook o Meta Business Manager.</li>
            <li>Ve a <strong>Configuración y Privacidad → Configuración → Aplicaciones y Sitios Web</strong>.</li>
            <li>Busca la aplicación <strong>LejoVista</strong>.</li>
            <li>Haz clic en <strong>Eliminar / Desvincular</strong>.</li>
            <li>Meta revoca el token automáticamente. En la siguiente verificación diaria, LejoVista marcará el token como revocado e invalidará el acceso.</li>
          </ol>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            Opción 3: Solicitud Manual por Correo Electrónico
          </h2>
          <p>
            Si deseas que eliminemos completamente la organización, los usuarios y todo registro histórico asociado:
          </p>
          <p style={{ marginTop: "8px" }}>
            Envía un correo a <code>privacy@lejovista.com</code> con el asunto <strong>&quot;Solicitud de Eliminación de Datos - [Nombre de tu Organización]&quot;</strong> desde el correo del administrador principal. Procesaremos la eliminación total y te enviaremos un código de confirmación en un plazo no mayor a 48 horas hábiles.
          </p>
        </section>
      </main>

      {/* Footer */}
      <footer style={{
        width: "100%",
        maxWidth: "960px",
        margin: "0 auto",
        padding: "24px",
        borderTop: "1px solid var(--border-subtle)",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        fontSize: "13px",
        color: "var(--color-ash)",
      }}>
        <span>© 2026 LejoVista. Todos los derechos reservados.</span>
        <div style={{ display: "flex", gap: "16px" }}>
          <Link href="/terms" style={{ color: "var(--color-fog)", textDecoration: "none" }}>Términos de Servicio</Link>
          <Link href="/privacy" style={{ color: "var(--color-fog)", textDecoration: "none" }}>Privacidad</Link>
          <Link href="/data-deletion" style={{ color: "var(--color-fog)", textDecoration: "none" }}>Eliminación de Datos</Link>
        </div>
      </footer>
    </div>
  );
}
