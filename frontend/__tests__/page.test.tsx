import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import Page from '../app/landing/page'

describe('Home Page', () => {
  it('renders a heading', () => {
    render(<Page />)
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent('LejoVista Platform')
  })
})
