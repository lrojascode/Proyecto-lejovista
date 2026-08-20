import { money, fmtInt } from '../lib/format'

describe('formato de moneda', () => {
  it('usa la convencion peruana: miles con coma, decimales con punto', () => {
    // Peru NO usa 7.417,19 (eso es Argentina/España/Colombia). Se puede
    // verificar con Intl: es-PE da "7,417.19", es-AR da "7.417,19".
    expect(money(7417.19)).toBe('S/. 7,417.19')
    expect(money(1000)).toBe('S/. 1,000.00')
    expect(money(0)).toBe('S/. 0.00')
    expect(money(-1234.5)).toBe('S/. -1,234.50')
    expect(fmtInt(789412)).toBe('789,412')
    expect(fmtInt(1234567)).toBe('1,234,567')
  })

  it('trata null y undefined como cero en vez de romper', () => {
    expect(money(null)).toBe('S/. 0.00')
    expect(money(undefined)).toBe('S/. 0.00')
    expect(fmtInt(null)).toBe('0')
  })

  it('coincide con el formateador del backend', () => {
    // Espejo de backend/app/utils/formatting.py. Si estos dos se separan, el
    // cliente ve dos escrituras del mismo numero el mismo dia — que es
    // exactamente el bug que este modulo cierra.
    const casos: [number, string][] = [
      [7417.19, 'S/. 7,417.19'],
      [999999.99, 'S/. 999,999.99'],
      [0.5, 'S/. 0.50'],
    ]
    for (const [valor, esperado] of casos) expect(money(valor)).toBe(esperado)
  })
})
