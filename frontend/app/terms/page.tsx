"use client";

import Link from "next/link";

export default function TermsOfService() {
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
          Términos de Servicio
        </h1>
        <p style={{ fontSize: "14px", color: "var(--color-ash)", marginBottom: "32px" }}>
          Última actualización: 19 de agosto de 2026
        </p>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            1. Aceptación de los Términos
          </h2>
          <p>
            Al registrarte, acceder o utilizar la plataforma LejoVista (&quot;el Servicio&quot;), aceptas quedar legalmente vinculado por estos Términos de Servicio y por nuestra Política de Privacidad. Si utilizas el Servicio en nombre de una empresa u organización, declaras tener la autoridad legal para vincular a dicha entidad.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            2. Descripción del Servicio y Conectividad con Meta Ads
          </h2>
          <p>
            LejoVista proporciona herramientas de análisis asistido por inteligencia artificial, diagnóstico determinístico de rendimiento, generación estructurada de borradores y sincronización de anuncios publicitarios mediante la API oficial de Meta Graph API.
          </p>
          <p style={{ marginTop: "10px" }}>
            El usuario es el único responsable de contar con los permisos y credenciales válidas en su cuenta publicitaria de Meta Business Manager.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            3. Control de Presupuesto y Activación Manual de Campañas
          </h2>
          <p>
            Para garantizar la máxima seguridad y control de tus activos publicitarios:
          </p>
          <ul style={{ paddingLeft: "20px", marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
            <li><strong>Límites de Presupuesto:</strong> La plataforma respeta de forma estricta los techos presupuestarios (Spend Caps) y presupuestos diarios configurados por la organización.</li>
            <li><strong>Creación Segura (Pausada por Defecto):</strong> Todas las campañas creadas a través de los flujos de LejoVista se publican en estado <code>PAUSED</code> (pausado) en Meta Ads. La activación en estado <code>ACTIVE</code> requiere siempre revisión y aprobación humana explícita del operador o administrador.</li>
          </ul>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            4. Cumplimiento de Políticas Publicitarias de Meta
          </h2>
          <p>
            El usuario es responsable de que todo material creativo, texto publicitario, imágenes, videos y destinos comerciales cumplan con las Políticas de Publicidad de Meta. LejoVista incluye catálogos de verificación preventiva por vertical (salud, finanzas, empleo, etc.), pero la aprobación final está sujeta al criterio de los sistemas de revisión de Meta.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            5. Disponibilidad y Cancelación
          </h2>
          <p>
            Puedes cancelar tu suscripción o desvincular tus cuentas publicitarias en cualquier momento. Al cancelar el servicio o eliminar tu cuenta, todos los tokens de acceso y credenciales se eliminan de forma irreversible de nuestras bases de datos.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            6. Contacto
          </h2>
          <p>
            Para cualquier consulta relacionada con estos términos, contáctanos en: <code>legal@lejovista.com</code>.
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
