import type { Config } from 'jest'
import nextJest from 'next/jest.js'

const createJestConfig = nextJest({
  // Provide the path to your Next.js app to load next.config.js and .env files in your test environment
  dir: './',
})

// Add any custom config to be passed to Jest
const config: Config = {
  coverageProvider: 'v8',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
    // `react-markdown` y su cadena (remark/micromark/unified/vfile…) son ESM
    // puro. next/jest antepone siempre `/node_modules/` a
    // transformIgnorePatterns y solo deja AGREGAR patrones, así que no hay
    // forma de pedir que las transforme sin meter ~25 paquetes en
    // `transpilePackages`, que encima afectaría el build de producción.
    // Se mockean: convertir markdown a HTML es responsabilidad de esa
    // librería, no de este repo. Lo que sí se testea es lo nuestro — que el
    // texto del asistente pase por el renderer y el del usuario no.
    '^react-markdown$': '<rootDir>/__mocks__/react-markdown.tsx',
    '^remark-gfm$': '<rootDir>/__mocks__/remark-gfm.ts',
  },
}

// createJestConfig is exported this way to ensure that next/jest can load the Next.js config which is async
export default createJestConfig(config)
