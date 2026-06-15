import '@testing-library/jest-dom'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Login from '../app/(auth)/login/page'
import { api } from '../lib/api'
import { safeRedirect } from '../lib/navigation'

// Mock the API module
jest.mock('../lib/api', () => ({
  api: {
    login: jest.fn(),
    signup: jest.fn()
  }
}))

// Mock the navigation module
jest.mock('../lib/navigation', () => ({
  safeRedirect: jest.fn()
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

  it('calls login API and stores session on success', async () => {
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
      expect(localStorage.getItem('token')).toBe('fake-token-123')
      expect(JSON.parse(localStorage.getItem('user') || '{}').full_name).toBe('Test User')
      expect(safeRedirect).toHaveBeenCalledWith('/')
    })
  })
})
