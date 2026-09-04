import { modeloSeleccionado, opcionesDelSelector } from '../lib/ai-models'

const CATALOGO = [
  { value: 'gpt-5.6-luna', label: 'GPT-5.6 Luna' },
  { value: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
]

describe('modeloSeleccionado', () => {
  it('muestra el modelo guardado cuando está en el catálogo', () => {
    expect(modeloSeleccionado('gpt-5.4-mini', CATALOGO)).toBe('gpt-5.4-mini')
  })

  it('muestra el modelo guardado AUNQUE el catálogo ya no lo ofrezca', () => {
    // La trampa: caer al primero de la lista hacía que el formulario mostrara
    // un modelo distinto del configurado. Guardar sin tocar nada cambiaba el
    // modelo y su precio, sin que nadie lo decidiera.
    expect(modeloSeleccionado('gpt-5.4-nano-retirado', CATALOGO)).toBe('gpt-5.4-nano-retirado')
    expect(modeloSeleccionado('gpt-5.4-nano-retirado', CATALOGO)).not.toBe(CATALOGO[0].value)
  })

  it('sin modelo guardado no inventa uno', () => {
    expect(modeloSeleccionado(null, CATALOGO)).toBe('')
    expect(modeloSeleccionado(undefined, CATALOGO)).toBe('')
  })

  it('un catálogo vacío no cambia el modelo guardado', () => {
    expect(modeloSeleccionado('gpt-5.4-mini', [])).toBe('gpt-5.4-mini')
  })
})

describe('opcionesDelSelector', () => {
  it('deja el catálogo intacto cuando el modelo guardado está en él', () => {
    expect(opcionesDelSelector('gpt-5.4-mini', CATALOGO)).toEqual(CATALOGO)
  })

  it('agrega el modelo guardado marcado cuando ya no se ofrece', () => {
    // Ocultarlo dejaría el <select> sin una opción que coincida con su valor,
    // y el navegador mostraría el primer elemento: la sustitución silenciosa
    // otra vez, cortesía del HTML.
    const opciones = opcionesDelSelector('modelo-retirado', CATALOGO)
    expect(opciones[0].value).toBe('modelo-retirado')
    expect(opciones[0].label).toContain('ya no se ofrece')
    expect(opciones).toHaveLength(CATALOGO.length + 1)
  })

  it('no duplica ni marca nada si no hay modelo guardado', () => {
    expect(opcionesDelSelector('', CATALOGO)).toEqual(CATALOGO)
  })
})
