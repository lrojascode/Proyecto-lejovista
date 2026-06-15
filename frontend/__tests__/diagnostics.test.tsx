import '@testing-library/jest-dom'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Diagnostics from '../app/(dashboard)/diagnostics/page'
import { api } from '../lib/api'

// Mock the API module
jest.mock('../lib/api', () => ({
  api: {
    getInsights: jest.fn(),
    getBriefHtml: jest.fn(),
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

  it('renders insights lists and iframe brief', async () => {
    const mockInsights = [
      { id: '1', level: 'CRITICAL', title: 'High CPL alert', description: 'CPL is too high', suggested_action_type: 'PAUSE' },
      { id: '2', level: 'WARNING', title: 'Budget warning', description: 'Daily budget limit close', suggested_action_type: 'ADJUST_BUDGET' }
    ]
    ;(api.getInsights as jest.Mock).mockResolvedValueOnce(mockInsights)
    ;(api.getBriefHtml as jest.Mock).mockReturnValue('http://localhost:8000/api/v1/reports/brief?token=fake-token')

    render(<Diagnostics />)

    // Wait for data to load
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Análisis y Diagnósticos de la IA/i })).toBeInTheDocument()
    })

    // Check alerts are displayed
    expect(screen.getByText('High CPL alert')).toBeInTheDocument()
    expect(screen.getByText('CPL is too high')).toBeInTheDocument()
    expect(screen.getByText('Budget warning')).toBeInTheDocument()

    // Check iframe loads with the correct source URL
    const iframe = screen.getByTitle('Master Brief Report')
    expect(iframe).toBeInTheDocument()
    expect(iframe).toHaveAttribute('src', 'http://localhost:8000/api/v1/reports/brief?token=fake-token')
  })

  it('handles dismissing an insight', async () => {
    const mockInsights = [
      { id: '1', level: 'CRITICAL', title: 'High CPL alert', description: 'CPL is too high', suggested_action_type: 'PAUSE' }
    ]
    ;(api.getInsights as jest.Mock).mockResolvedValueOnce(mockInsights)
    ;(api.getBriefHtml as jest.Mock).mockReturnValue('http://localhost:8000/api/v1/reports/brief?token=fake-token')
    ;(api.dismissInsight as jest.Mock).mockResolvedValueOnce({ status: 'ok' })

    render(<Diagnostics />)

    await waitFor(() => {
      expect(screen.getByText('High CPL alert')).toBeInTheDocument()
    })

    const dismissBtn = screen.getByRole('button', { name: /✕ Descartar/i })
    fireEvent.click(dismissBtn)

    await waitFor(() => {
      expect(api.dismissInsight).toHaveBeenCalledWith('1')
      // Element is removed from list
      expect(screen.queryByText('High CPL alert')).not.toBeInTheDocument()
    })
  })
})
