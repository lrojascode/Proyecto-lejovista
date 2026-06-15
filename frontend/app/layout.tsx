import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LejoVista — IA Meta Ads Automation",
  description: "Plataforma SaaS para análisis y optimización autónoma de campañas Meta Ads con Inteligencia Artificial.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body>
        {children}
      </body>
    </html>
  );
}
