"""Formato de cifras del producto. Módulo hoja a propósito: no importa nada de
la app, así cualquier otro módulo puede usarlo sin riesgo de ciclo.

Existe porque este formato ya se rompió dos veces.

La primera: había dos copias idénticas de `_money` —una en `facts.py`, otra en
`questions.py`— y al corregir solo una, el mismo monto salía de dos formas en
mensajes consecutivos. Por eso hay UNA definición, y vive en `utils` y no
dentro del chat: el chat, el PDF y las narrativas del reporte hablan del mismo
dinero.

La segunda: la copia "arreglada" agrupaba como `7.417,19`, que es la
convención de Argentina, España y Colombia. **Perú no usa esa.** Perú agrupa
los miles con COMA y separa los decimales con PUNTO — `7,417.19`— igual que
México y Estados Unidos. Se puede verificar contra los datos de locale:

    new Intl.NumberFormat("es-PE").format(7417.19)  // "7,417.19"
    new Intl.NumberFormat("es-AR").format(7417.19)  // "7.417,19"

El espejo en el frontend es `lib/format.ts`, que lo obtiene de `Intl` con el
locale `es-PE` en vez de armarlo a mano. Si una cambia, la otra también.

El símbolo se deja como `S/.` porque es el que usa todo el producto. El oficial
es `S/` sin punto, pero cambiarlo es una decisión de marca aparte.

Los valores numéricos de fondo (`7417.19`) no cambian nunca — esto solo formatea
el texto que se muestra.
"""
from typing import Optional


def money(value: Optional[float]) -> str:
    """`7417.19` -> `S/. 7,417.19` (convención peruana)."""
    v = float(value or 0)
    sign = "-" if v < 0 else ""
    return f"S/. {sign}{abs(v):,.2f}"


def fmt_int(value: Optional[int]) -> str:
    """`789412` -> `789,412`.

    Ojo: nunca aplicar un `.replace()` de separadores sobre una cadena que ya
    contenga un `money()` — convertiría `S/. 7,417.19` en otra cosa y rompería
    el grounding. Por eso esto recibe un entero y no un string.
    """
    return f"{int(value or 0):,}"
