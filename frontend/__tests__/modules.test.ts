import { moduleForPath, MODULE_ROUTES } from '../lib/modules'

describe('moduleForPath', () => {
  it('mapea cada pantalla del menú a su módulo', () => {
    expect(moduleForPath('/')).toBe('resumen')
    expect(moduleForPath('/campaigns')).toBe('campanas')
    expect(moduleForPath('/chat')).toBe('chat')
    expect(moduleForPath('/reporte')).toBe('reporte')
    expect(moduleForPath('/diagnostics')).toBe('diagnosticos')
    expect(moduleForPath('/actions')).toBe('acciones')
    expect(moduleForPath('/ai-usage')).toBe('ai_usage')
    expect(moduleForPath('/settings')).toBe('settings')
  })

  it('las subrutas pertenecen a la pantalla de su padre', () => {
    // Apagar Campañas tiene que apagar tambien el detalle y la creacion, o el
    // cliente entra igual por una URL profunda.
    expect(moduleForPath('/campaigns/123')).toBe('campanas')
    expect(moduleForPath('/campaigns/new')).toBe('campanas')
    expect(moduleForPath('/campaigns/learning')).toBe('campanas')
  })

  it('la raiz no se come el resto del menú', () => {
    // La trampa: comparando "/" por prefijo, TODA ruta seria el Resumen y
    // apagarlo dejaria al cliente sin ninguna pantalla.
    expect(moduleForPath('/settings')).not.toBe('resumen')
    expect(moduleForPath('/chat')).not.toBe('resumen')
  })

  it('no confunde una ruta que solo comparte prefijo de texto', () => {
    expect(moduleForPath('/campaigns-archivadas')).toBeNull()
  })

  it('una ruta que no es del menú no pertenece a ningun módulo', () => {
    expect(moduleForPath('/loquesea')).toBeNull()
  })

  it('/admin no pertenece a ningun módulo de cliente', () => {
    // El panel de plataforma no es un modulo contratable: si cayera en uno, la
    // redireccion del layout lo sacaria de ahi al primer cliente con modulos
    // recortados. Su gate real es el 403 del backend.
    expect(moduleForPath('/admin')).toBeNull()
  })

  it('todas las rutas del mapa son alcanzables', () => {
    for (const r of MODULE_ROUTES) {
      expect(moduleForPath(r.path)).toBe(r.module)
    }
  })
})
