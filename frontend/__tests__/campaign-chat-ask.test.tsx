import '@testing-library/jest-dom'
import { render, screen, waitFor } from '@testing-library/react'
import AnalystChatPage from '../app/(dashboard)/chat/page'
import { api } from '../lib/api'

jest.mock('../lib/api', () => ({
  api: {
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

const textMsg = (text: string, quick_replies?: string[]) => ({
  id: `m-${Math.random()}`, role: 'assistant', type: 'text', text,
  quick_replies: quick_replies || null, created_at: '2026-01-01T00:00:00',
})

function askSession() {
  return {
    id: 'session-ask-1', state: 'BROWSING', status: 'OPEN', mode: 'ASK', ad_account_id: 'acc-1', draft_id: null,
    slots: {}, messages: [
      textMsg(
        '¡Hola! Soy tu analista de Meta Ads. ¿Qué querés saber sobre el rendimiento de tu cuenta?',
        ['¿Cómo vengo este mes?', '¿Cuál campaña rinde mejor?']
      ),
    ],
    created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00',
  }
}

describe('Analyst Chat Page (/chat)', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    Storage.prototype.getItem = jest.fn(() => null)
    Storage.prototype.setItem = jest.fn()
    Storage.prototype.removeItem = jest.fn()
  })

  it('inicia sesión en modo ASK y muestra quick replies analíticos', async () => {
    ;(api.startCampaignChat as jest.Mock).mockResolvedValueOnce(askSession())
    render(<AnalystChatPage />)

    await waitFor(() => {
      expect(screen.getByText(/Soy tu analista de Meta Ads/i)).toBeInTheDocument()
      expect(screen.getByText('¿Cómo vengo este mes?')).toBeInTheDocument()
      expect(screen.getByText('¿Cuál campaña rinde mejor?')).toBeInTheDocument()
    })
    expect(api.startCampaignChat).toHaveBeenCalledWith('acc-1', 'ASK')
  })
})
