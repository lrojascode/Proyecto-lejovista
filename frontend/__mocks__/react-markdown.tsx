// Sustituto de `react-markdown` en los tests: ESM puro que next/jest no
// transforma (ver moduleNameMapper en jest.config.ts). Renderiza el texto tal
// cual dentro de un marcador, para poder afirmar QUÉ pasa por el renderer sin
// depender de cómo convierte el markdown la librería real.
export default function ReactMarkdown({ children }: { children?: React.ReactNode }) {
  return <div data-testid="markdown">{children}</div>;
}
