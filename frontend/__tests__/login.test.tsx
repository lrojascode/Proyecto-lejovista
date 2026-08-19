import '@testing-library/jest-dom'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Login from '../app/(auth)/login/page'
import { api } from '../lib/api'

// Mock the API module
jest.mock('../lib/api', () => ({
  api: {
    login: jest.fn(),
    signup: jest.fn(),
    getTenantBrand: jest.fn().mockResolvedValue({ name: "LejoVista", display_name: "LejoVista", logo_url: null, accent_color: null }),
  }
}))

// (S-2) La página ya no navega con window.location — usa el router de Next
// para preservar el access token en memoria (lib/api.ts) entre login y el
// dashboard, en vez de perderlo en una recarga completa.
const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))

describe('Login Page', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    localStorage.clear()
  })

  it('renders login form by default', () => {
    render(<Login />)
    expect(screen.getByRole('heading', { name: /LejoVista/i })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('correo@empresa.com')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('••••••••')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Iniciar Sesión/i })).toBeInTheDocument()
  })

  it('toggles between login and signup modes', () => {
    render(<Login />)
    
    // Toggle to signup
    const toggleBtn = screen.getByRole('button', { name: /¿No tienes cuenta\? Regístrate aquí/i })
    fireEvent.click(toggleBtn)
    
    expect(screen.getByPlaceholderText('Alejandro Rojas')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Cables MTV S.A.C.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Crear Cuenta/i })).toBeInTheDocument()

    // Toggle back to login
    const toggleBackBtn = screen.getByRole('button', { name: /¿Ya tienes cuenta\? Inicia sesión/i })
    fireEvent.click(toggleBackBtn)

    expect(screen.queryByPlaceholderText('Alejandro Rojas')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Iniciar Sesión/i })).toBeInTheDocument()
  })

  it('calls login API and navigates to the dashboard on success', async () => {
    // (S-2) api.login guarda la sesión (access token en memoria, user en
    // localStorage) — responsabilidad de lib/api.ts, no de esta página. Aquí
    // solo se prueba que la página llama a login y navega al éxito.
    const mockResponse = {
      access_token: 'fake-token-123',
      user: { id: 1, email: 'test@example.com', full_name: 'Test User' }
    }
    ;(api.login as jest.Mock).mockResolvedValueOnce(mockResponse)

    render(<Login />)

    fireEvent.change(screen.getByPlaceholderText('correo@empresa.com'), { target: { value: 'test@example.com' } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'password123' } })

    fireEvent.click(screen.getByRole('button', { name: /Iniciar Sesión/i }))

    await waitFor(() => {
      expect(api.login).toHaveBeenCalledWith({ email: 'test@example.com', password: 'password123' })
      expect(mockPush).toHaveBeenCalledWith('/')
    })
  })
})
