import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Empaqueta en .next/standalone solo los archivos y node_modules que la app
  // necesita para correr. Permite una imagen Docker mínima (~250 MB) sin el
  // node_modules completo. Ver frontend/Dockerfile (etapa runner).
  output: "standalone",
};

export default nextConfig;
