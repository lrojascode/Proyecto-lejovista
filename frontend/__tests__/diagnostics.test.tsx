import '@testing-library/jest-dom'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Diagnostics from '../app/(dashboard)/diagnostics/page'
import { api } from '../lib/api'
import { money } from '../lib/format'

// Mock the API module
jest.mock('../lib/api', () => ({
  api: {
    getInsights: jest.fn(),
    getBriefData: jest.fn(),
    dismissInsight: jest.fn()
  }
}))

describe('Diagnostics Page', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders loading state initially', () => {
    ;(api.getInsights as jest.Mock).mockReturnValue(new Promise(() => {})) // Never resolves
    render(<Diagnostics />)
    expect(screen.getByText(/Cargando diagnósticos/i)).toBeInTheDocument()
  })

  it('renders insights lists and native brief', async () => {
    const mockInsights = [
      { id: '1', level: 'CRITICAL', title: 'High CPL alert', description: 'CPL is too high', suggested_action_type: 'PAUSE' },
      { id: '2', level: 'WARNING', title: 'Budget warning', description: 'Daily budget limit close', suggested_action_type: 'ADJUST_BUDGET' }
    ]
    const mockBriefData = {
      organization_name: 'Brief Data Corp',
      ad_account_name: 'Brief Data Account',
      external_account_id: '77777777',
      generated_at: '2026-06-27T12:00:00Z',
      narrative: 'Narrativa de IA de prueba nativa',
      campaigns_data: {
        'camp_1': { name: 'Campaña de Prueba', spend: 120.0, conversions: 12, ctr: 1.8, budget: 60.0, status: 'ACTIVE' }
      }
    }
    ;(api.getInsights as jest.Mock).mockResolvedValueOnce(mockInsights)
    ;(api.getBriefData as jest.Mock).mockResolvedValue(mockBriefData)

    render(<Diagnostics />)

    // Wait for data to load
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Análisis y Diagnósticos de la IA/i })).toBeInTheDocument()
    })

    // Check alerts are displayed
    expect(screen.getByText('High CPL alert')).toBeInTheDocument()
    expect(screen.getByText('CPL is too high')).toBeInTheDocument()
    expect(screen.getByText('Budget warning')).toBeInTheDocument()

    // Check native brief sections load with correct content
    expect(screen.getByText('Narrativa de IA de prueba nativa')).toBeInTheDocument()
    expect(screen.getByText('Campaña de Prueba')).toBeInTheDocument()
    expect(screen.getAllByText(money(120))[0]).toBeInTheDocument()
  })

  it('handles dismissing an insight', async () => {
    const mockInsights = [
      { id: '1', level: 'CRITICAL', title: 'High CPL alert', description: 'CPL is too high', suggested_action_type: 'PAUSE' }
    ]
    ;(api.getInsights as jest.Mock).mockResolvedValueOnce(mockInsights)
    ;(api.getBriefData as jest.Mock).mockResolvedValue({
      organization_name: 'Brief Data Corp',
      ad_account_name: 'Brief Data Account',
      external_account_id: '77777777',
      generated_at: '2026-06-27T12:00:00Z',
      narrative: 'Narrativa de IA de prueba nativa',
      campaigns_data: {}
    })
    ;(api.dismissInsight as jest.Mock).mockResolvedValueOnce({ status: 'ok' })

    render(<Diagnostics />)

    await waitFor(() => {
      expect(screen.getByText('High CPL alert')).toBeInTheDocument()
    })

    const dismissBtn = screen.getByRole('button', { name: /Descartar/i })
    fireEvent.click(dismissBtn)

    await waitFor(() => {
      expect(api.dismissInsight).toHaveBeenCalledWith('1')
      // Element is removed from list
      expect(screen.queryByText('High CPL alert')).not.toBeInTheDocument()
    })
  })
})
