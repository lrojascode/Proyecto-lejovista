import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import Page from '../app/landing/page'
import PrivacyPage from '../app/privacy/page'
import TermsPage from '../app/terms/page'
import DataDeletionPage from '../app/data-deletion/page'

describe('Public Marketing & Legal Pages', () => {
  it('renders landing page headline and value propositions', () => {
    render(<Page />)
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent(/Meta Ads/i)
    expect(screen.getByText(/Términos de Servicio/i)).toBeInTheDocument()
    expect(screen.getByText(/Política de Privacidad/i)).toBeInTheDocument()
  })

  it('renders Privacy Policy page', () => {
    render(<PrivacyPage />)
    expect(screen.getByRole('heading', { name: /Política de Privacidad/i })).toBeInTheDocument()
    expect(screen.getAllByText(/Meta Graph API/i).length).toBeGreaterThan(0)
  })

  it('renders Terms of Service page', () => {
    render(<TermsPage />)
    expect(screen.getByRole('heading', { name: /Términos de Servicio/i })).toBeInTheDocument()
    expect(screen.getByText(/PAUSED/i)).toBeInTheDocument()
  })

  it('renders Data Deletion instructions page', () => {
    render(<DataDeletionPage />)
    expect(screen.getByRole('heading', { name: /Eliminación de Datos/i })).toBeInTheDocument()
    expect(screen.getByText(/privacy@lejovista.com/i)).toBeInTheDocument()
  })
})
