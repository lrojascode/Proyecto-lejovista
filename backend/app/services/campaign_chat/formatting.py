"""Formato de cifras del chat. Módulo hoja a propósito: no importa nada del
paquete, así cualquier otro módulo puede usarlo sin riesgo de ciclo.

Existe porque este formato ya se rompió una vez. Había dos copias idénticas de
`_money` —una en `facts.py`, otra en `questions.py`— y al corregir solo una el
mismo monto salía `S/. 7.417,19` en una respuesta y `S/.7417.19` en la
siguiente. Una sola definición es la única forma de que eso no vuelva a pasar.

Convención: es-PE — separador de miles con punto, decimales con coma. Es la
convención del CHAT, no la del resto del producto todavía: el PDF, el reporte y
el dashboard siguen con `S/.7417.19` (ver execution/01). Los facts numéricos
detrás (`7417.19`) no cambian nunca — esto solo formatea el texto que se muestra.
"""
from typing import Optional


def money(value: Optional[float]) -> str:
    """`2399.97` -> `S/. 2.399,97`."""
    v = float(value or 0)
    sign = "-" if v < 0 else ""
    entero, dec = f"{abs(v):.2f}".split(".")
    entero_agrupado = f"{int(entero):,}".replace(",", ".")
    return f"S/. {sign}{entero_agrupado},{dec}"


def fmt_int(value: Optional[int]) -> str:
    """`789412` -> `789.412`.

    Ojo: el `.replace(",", ".")` va sobre el entero ya formateado, nunca sobre
    una cadena que contenga un `money()` — eso convertiría `S/. 7.417,19` en
    `S/. 7.417.19` y rompería el grounding.
    """
    return f"{int(value or 0):,}".replace(",", ".")
