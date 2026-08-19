"use client";

import Link from "next/link";

export default function PrivacyPolicy() {
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
          Política de Privacidad
        </h1>
        <p style={{ fontSize: "14px", color: "var(--color-ash)", marginBottom: "32px" }}>
          Última actualización: 19 de agosto de 2026
        </p>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            1. Información General y Responsable del Tratamiento
          </h2>
          <p>
            LejoVista (&quot;nosotros&quot;, &quot;la plataforma&quot;) es una plataforma de software como servicio (SaaS) orientada a la
            gestión autónoma, análisis, diagnóstico y optimización de campañas publicitarias en Meta Ads (Facebook e Instagram).
            Esta Política de Privacidad describe cómo recopilamos, usamos, almacenamos y protegemos la información cuando utilizas
            nuestros servicios o conectas tus cuentas comerciales y publicitarias.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            2. Datos Recopilados a través de Meta Graph API
          </h2>
          <p>
            Cuando vinculas tu cuenta de Meta Ads con LejoVista, solicitamos permisos estrictamente necesarios mediante la API de Meta:
          </p>
          <ul style={{ paddingLeft: "20px", marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
            <li><strong>Métricas de rendimiento publicitario:</strong> Impresiones, clics, gasto, conversiones (leads/mensajes), CPL, CTR y frecuencias agregadas.</li>
            <li><strong>Estructura de campañas:</strong> Identificadores de campañas, conjuntos de anuncios (adsets), creativos y estados de entrega.</li>
            <li><strong>Páginas y WhatsApp Business:</strong> Identificadores y nombres de páginas de Facebook vinculadas a la cuenta publicitaria para la asignación de destinos en la creación de campañas.</li>
          </ul>
          <p style={{ marginTop: "12px" }}>
            <strong>Privacidad de conversaciones:</strong> LejoVista NO lee ni almacena el contenido de los mensajes personales o conversaciones de WhatsApp ni de Messenger de tus clientes.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            3. Uso de la Información e Inteligencia Artificial
          </h2>
          <p>
            Utilizamos los datos recopilados exclusivamente para:
          </p>
          <ul style={{ paddingLeft: "20px", marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
            <li>Sincronizar y mostrar el estado financiero y métricas de tus anuncios en tiempo real.</li>
            <li>Generar diagnósticos, resúmenes analíticos y detectar anomalías mediante modelos de lenguaje (LLM) autorizados por tu organización.</li>
            <li>Ejecutar automatizaciones y optimizaciones presupuestarias bajo tus reglas estrictas de negocio.</li>
          </ul>
          <p style={{ marginTop: "12px" }}>
            <strong>Regla de Gobernanza AI:</strong> Ningún dato financiero o de métricas es alucinado o estimado arbitrariamente por modelos generativos. Todos los cálculos se realizan de forma determinística en Python contra los registros oficiales de Meta Graph API.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            4. Seguridad, Cifrado y Aislamiento de Inquilinos (Multi-Tenant)
          </h2>
          <p>
            Los tokens de acceso de Meta y claves de API de modelos de IA se cifran en reposo con el estándar de cifrado autenticado <strong>AES-256 (Fernet)</strong>.
            Cada organización cuenta con aislamiento lógico y estricto de base de datos (`organization_id` y aislamiento por subdominio), garantizando que ningún usuario o modelo acceda a datos de otra empresa.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            5. Retención y Eliminación de Datos (Derechos ARCO)
          </h2>
          <p>
            Conservamos tus datos mientras mantengas una cuenta activa en LejoVista. Puedes solicitar la eliminación total de tus datos
            en cualquier momento desde el panel de Configuración (archivando y eliminando la cuenta publicitaria) o siguiendo las instrucciones detalladas en nuestra página de <Link href="/data-deletion" style={{ color: "var(--color-signal-teal)" }}>Eliminación de Datos</Link>.
          </p>
        </section>

        <section style={{ marginBottom: "28px" }}>
          <h2 style={{ fontSize: "20px", fontWeight: 510, color: "var(--color-paper)", marginBottom: "12px" }}>
            6. Contacto
          </h2>
          <p>
            Si tienes dudas o inquietudes sobre esta política o el tratamiento de tus datos, puedes contactar a nuestro oficial de privacidad en: <code>privacy@lejovista.com</code>.
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
