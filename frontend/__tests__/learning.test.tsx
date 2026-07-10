import '@testing-library/jest-dom'
import { render, screen, waitFor } from '@testing-library/react'
import LearningHistoryPage from '../app/(dashboard)/campaigns/learning/page'
import { api } from '../lib/api'

// E-1: timeline del aprendizaje de la IA (GET /campaigns/learning-history).
jest.mock('../lib/api', () => ({
  api: {
    getLearningHistory: jest.fn(),
  },
}))

const entry = (overrides: Partial<any> = {}) => ({
  id: 'log-1',
  product: 'Cable THHN',
  risk_level: 'LOW',
  status: 'CREATED',
  params: { budget: 100, days: 7 },
  analysis: {
    summary: 'Hay histórico ganador: parto de esa configuración.',
    mode: 'exploration',
    converged: false,
    maturity_reason: 'Solo hay 1 campaña con conversaciones.',
    strengths: [],
    weaknesses: [],
    changes_proposed: [
      { change: 'Excluir los placements sin retorno', why: 'Concentra el presupuesto donde sí convierte.' },
    ],
    past_failures: ["'Vieja' gastó S/.20.00 sin ninguna conversación."],
    evaluation_window_days: 7,
    confidence: 'media',
  },
  maturity: { converged: false, mode: 'exploration', reason: '...' },
  outcome: null,
  created_at: '2026-07-09T10:00:00Z',
  ...overrides,
})

describe('Learning History Page', () => {
  beforeEach(() => jest.clearAllMocks())

  it('renders loading state initially', () => {
    ;(api.getLearningHistory as jest.Mock).mockReturnValue(new Promise(() => {}))
    render(<LearningHistoryPage />)
    expect(screen.getByText(/Cargando historial de aprendizaje/i)).toBeInTheDocument()
  })

  it('renders empty state with CTA when there is no history', async () => {
    ;(api.getLearningHistory as jest.Mock).mockResolvedValueOnce([])
    render(<LearningHistoryPage />)
    await waitFor(() => {
      expect(screen.getByText(/todavía no tiene historial/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: /Crear la primera campaña/i })).toBeInTheDocument()
  })

  it('renders a timeline entry with mode, risk, reasoning and pending outcome', async () => {
    ;(api.getLearningHistory as jest.Mock).mockResolvedValueOnce([entry()])
    render(<LearningHistoryPage />)

    await waitFor(() => {
      expect(screen.getByText('Cable THHN')).toBeInTheDocument()
    })
    expect(screen.getByText(/Modo exploración/i)).toBeInTheDocument()
    expect(screen.getByText(/Riesgo bajo/i)).toBeInTheDocument()
    expect(screen.getByText(/Hay histórico ganador/i)).toBeInTheDocument()
    expect(screen.getByText(/Excluir los placements sin retorno/i)).toBeInTheDocument()
    // Sin outcome (Fase B pendiente): estado "en evaluación".
    expect(screen.getByText(/en evaluación/i)).toBeInTheDocument()
  })

  it('renders the outcome verdict when present', async () => {
    ;(api.getLearningHistory as jest.Mock).mockResolvedValueOnce([
      entry({
        outcome: { verdict: 'WIN', cost_per_conv: 1.8, expected_cost_per_conv: 2.0 },
      }),
    ])
    render(<LearningHistoryPage />)

    await waitFor(() => {
      expect(screen.getByText(/Funcionó/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/S\/\.1\.80 por conversación/i)).toBeInTheDocument()
  })

  it('renders the full outcome with figures and reason (Fase 1)', async () => {
    ;(api.getLearningHistory as jest.Mock).mockResolvedValueOnce([
      entry({
        status: 'EVALUATED',
        outcome: {
          verdict: 'LOSS',
          cost_per_conv: 11.0,
          expected_cost_per_conv: 5.0,
          spend: 220.0,
          conversations: 20,
          reason: 'S/.11.00 por conversación: más del doble del costo esperado (~S/.5.00).',
        },
      }),
    ])
    render(<LearningHistoryPage />)

    await waitFor(() => {
      expect(screen.getByText(/No funcionó/i)).toBeInTheDocument()
    })
    expect(screen.getAllByText(/S\/\.11\.00 por conversación/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/esperado ~S\/\.5\.00/i)).toBeInTheDocument()
    expect(screen.getByText(/20 conversación\(es\) con S\/\.220\.00 de gasto/i)).toBeInTheDocument()
    expect(screen.getByText(/más del doble del costo esperado/i)).toBeInTheDocument()
  })

  it('renders NOT_ACTIVATED with its own label, not as neutral', async () => {
    ;(api.getLearningHistory as jest.Mock).mockResolvedValueOnce([
      entry({
        status: 'EVALUATED',
        outcome: {
          verdict: 'NOT_ACTIVATED',
          cost_per_conv: null,
          reason: 'La creación de la campaña falló en Meta.',
        },
      }),
    ])
    render(<LearningHistoryPage />)

    await waitFor(() => {
      expect(screen.getByText(/No llegó a activarse/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/Neutral/i)).not.toBeInTheDocument()
    expect(screen.getByText(/falló en Meta/i)).toBeInTheDocument()
  })

  it('shows the error message when the API fails', async () => {
    ;(api.getLearningHistory as jest.Mock).mockRejectedValueOnce(new Error('falló la red'))
    render(<LearningHistoryPage />)
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('falló la red')
    })
  })
})
