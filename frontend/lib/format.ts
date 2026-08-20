/** Formato de cifras del producto.
 *
 * Espejo de `backend/app/utils/formatting.py`: el mismo monto tiene que
 * escribirse igual en el chat, el dashboard, el reporte y el PDF. Antes no era
 * así —el chat decía `S/. 7.417,19` y el PDF `S/.7417.19` del mismo dato— y el
 * cliente veía dos escrituras del mismo número el mismo día.
 *
 * Convención: es-PE, separador de miles con punto y decimales con coma.
 * Si esta cambia, la de Python también.
 */
const MONEY = new Intl.NumberFormat("es-PE", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const INT = new Intl.NumberFormat("es-PE", { maximumFractionDigits: 0 });

/** `7417.19` → `S/. 7.417,19` */
export function money(value: number | null | undefined): string {
  return `S/. ${MONEY.format(Number(value ?? 0))}`;
}

/** `789412` → `789.412` */
export function fmtInt(value: number | null | undefined): string {
  return INT.format(Number(value ?? 0));
}
