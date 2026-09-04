/**
 * Qué modelo de IA muestra el selector de Configuración (spec 10).
 *
 * Vive aparte de la pantalla porque encierra una decisión con consecuencias
 * de dinero, y una decisión así tiene que poder probarse sin montar el
 * formulario entero.
 *
 * La regla es una sola: **se muestra el modelo guardado**, esté o no en el
 * catálogo. Antes, un modelo ausente del catálogo se reemplazaba por el
 * primero de la lista; el formulario mostraba entonces un modelo distinto del
 * configurado, y guardar sin tocar nada lo cambiaba —junto con su precio— sin
 * que nadie lo decidiera. Es el mismo cambio silencioso que la spec elimina en
 * el backend, y colarse por la interfaz no lo hace menos silencioso.
 */

export type OpcionDeModelo = { value: string; label: string };

/** El valor que debe quedar seleccionado al cargar la pantalla. */
export function modeloSeleccionado(
  modeloGuardado: string | null | undefined,
  _catalogo: OpcionDeModelo[],
): string {
  // El catálogo NO participa: se recibe para que la firma diga que se
  // consideró y se descartó a propósito.
  return (modeloGuardado ?? "").trim();
}

/**
 * Las opciones del desplegable, con el modelo guardado incluido si el catálogo
 * ya no lo ofrece.
 *
 * Se marca, no se oculta. Ocultarlo dejaría el `select` sin una opción que
 * coincida con su valor, y el navegador mostraría el primer elemento — o sea,
 * la sustitución silenciosa otra vez, esta vez cortesía del HTML.
 */
export function opcionesDelSelector(
  modeloGuardado: string | null | undefined,
  catalogo: OpcionDeModelo[],
): OpcionDeModelo[] {
  const guardado = (modeloGuardado ?? "").trim();
  if (!guardado || catalogo.some((m) => m.value === guardado)) {
    return catalogo;
  }
  return [{ value: guardado, label: `${guardado} (ya no se ofrece)` }, ...catalogo];
}
