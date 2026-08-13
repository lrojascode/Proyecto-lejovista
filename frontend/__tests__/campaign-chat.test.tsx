import '@testing-library/jest-dom'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import CampaignsList from '../app/(dashboard)/campaigns/page'
import CampaignChatPage from '../app/(dashboard)/campaigns/chat/page'
import { api } from '../lib/api'

jest.mock('../lib/api', () => ({
  api: {
    getRecentCampaigns: jest.fn(),
    startCampaignChat: jest.fn(),
    getCampaignChatSession: jest.fn(),
    sendCampaignChatMessage: jest.fn(),
    decideCampaignChatCard: jest.fn(),
    advanceCampaignChat: jest.fn(),
    uploadCampaignImage: jest.fn(),
    uploadCampaignVideo: jest.fn(),
  },
}))

jest.mock('../components/providers/WebSocketProvider', () => ({
  useWebSocket: () => ({ lastEvent: null, isConnected: false }),
}))

jest.mock('../components/providers/AdAccountProvider', () => ({
  useAdAccount: () => ({
    accounts: [{ id: 'acc-1', name: 'Cuenta 1', external_account_id: 'act_123', status: 'ACTIVE' }],
    activeAdAccountId: 'acc-1',
    setActiveAdAccountId: jest.fn(),
    loading: false,
  }),
}))

const textMsg = (text: string, role: 'assistant' | 'user' = 'assistant') => ({
  id: `m-${Math.random()}`, role, type: 'text', text, created_at: '2026-01-01T00:00:00',
})

const reviewCard = {
  id: 'm-review', role: 'assistant', type: 'review_card', created_at: '2026-01-01T00:00:00',
  card_id: 'card-1', title: 'Esta es tu campaña, ¿está bien?',
  fields: [{ key: 'budget', label: 'Presupuesto', value: 'S/. 100.00 (total)', source: 'ai_suggestion', editable: true }],
  copy_preview: { headline: ['A', 'B'], primary_text: ['Texto de prueba'], description: 'desc', welcome_message: 'hola' },
  evidence: [],
  actions: [
    { action: 'accept', label: 'Aceptar', style: 'primary' },
    { action: 'change', label: 'Cambiar algo', style: 'neutral' },
    { action: 'reject', label: 'Cancelar', style: 'danger' },
  ],
}

function baseSession(overrides: any = {}) {
  return {
    id: 'session-1', state: 'ASK_GOAL', status: 'OPEN', ad_account_id: 'acc-1', draft_id: null,
    slots: {}, messages: [textMsg('¡Hola! ¿Qué querés promocionar?')],
    created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00',
    ...overrides,
  }
}

describe('Campaigns list — botón Chat', () => {
  it('el botón Chat está resaltado (btn-primary) y Crear Campaña queda secundario', async () => {
    ;(api.getRecentCampaigns as jest.Mock).mockResolvedValueOnce([])
    render(<CampaignsList />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /Todas las Campañas/i })).toBeInTheDocument())

    const chatLink = screen.getByRole('link', { name: /Chat/i })
    const newLink = screen.getByRole('link', { name: /Crear Campaña/i })
    expect(chatLink).toHaveClass('btn-primary')
    expect(newLink).toHaveClass('btn-secondary')
    expect(chatLink).toHaveAttribute('href', '/campaigns/chat')
  })
})

describe('Campaign chat page', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    Storage.prototype.getItem = jest.fn(() => null)
    Storage.prototype.setItem = jest.fn()
    Storage.prototype.removeItem = jest.fn()
  })

  it('crea una sesión y pinta el saludo', async () => {
    ;(api.startCampaignChat as jest.Mock).mockResolvedValueOnce(baseSession())
    render(<CampaignChatPage />)

    await waitFor(() => {
      expect(screen.getByText(/¿Qué querés promocionar\?/i)).toBeInTheDocument()
    })
    expect(api.startCampaignChat).toHaveBeenCalledWith('acc-1')
  })

  it('Aceptar en la tarjeta de revisión llama a decideCampaignChatCard con accept', async () => {
    ;(api.startCampaignChat as jest.Mock).mockResolvedValueOnce(
      baseSession({ state: 'REVIEW_PLAN', messages: [textMsg('hola'), reviewCard] })
    )
    ;(api.decideCampaignChatCard as jest.Mock).mockResolvedValueOnce(
      baseSession({ state: 'CONFIRM_RISK', messages: [textMsg('hola'), reviewCard] })
    )

    render(<CampaignChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Esta es tu campaña, ¿está bien?')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Aceptar' }))

    await waitFor(() => {
      expect(api.decideCampaignChatCard).toHaveBeenCalledWith(
        'session-1',
        expect.objectContaining({ cardId: 'card-1', decision: 'accept' })
      )
    })
  })

  it('Cancelar en la tarjeta de revisión llama a decideCampaignChatCard con reject', async () => {
    ;(api.startCampaignChat as jest.Mock).mockResolvedValueOnce(
      baseSession({ state: 'REVIEW_PLAN', messages: [textMsg('hola'), reviewCard] })
    )
    ;(api.decideCampaignChatCard as jest.Mock).mockResolvedValueOnce(
      baseSession({ state: 'ABANDONED', status: 'CLOSED_CANCELLED', messages: [textMsg('hola'), reviewCard, textMsg('Listo, no se creó nada.')] })
    )

    render(<CampaignChatPage />)
    await waitFor(() => screen.getByText('Esta es tu campaña, ¿está bien?'))
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))

    await waitFor(() => {
      expect(api.decideCampaignChatCard).toHaveBeenCalledWith(
        'session-1',
        expect.objectContaining({ cardId: 'card-1', decision: 'reject' })
      )
    })
  })

  it('muestra un error accionable cuando no se puede iniciar la sesión (sin página con WhatsApp)', async () => {
    ;(api.startCampaignChat as jest.Mock).mockRejectedValueOnce(
      new Error('Se requiere exactamente 1 página de Facebook activa con WhatsApp conectado.')
    )
    render(<CampaignChatPage />)

    await waitFor(() => {
      expect(screen.getByText(/No podemos empezar todavía/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/página de Facebook activa con WhatsApp/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Ir a Configuración/i })).toHaveAttribute('href', '/settings')
  })
})
