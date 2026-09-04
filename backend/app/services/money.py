"""Dinero exacto para las decisiones que autorizan gasto (spec 02).

El problema no es de formato, es de autorización. Una comparación del tipo
"lo gastado más lo que se propone no supera el límite" resuelta en coma flotante
binaria puede autorizar un gasto que EXCEDE el límite, o rechazar uno que cabe
exactamente, por un residuo de representación. En una compuerta que decide si se
gasta dinero real eso es un fallo de autorización.

Regla de la spec: `Decimal` (o unidades enteras mínimas) en todo lo que
participa de una decisión de gasto. La coma flotante puede seguir usándose para
métricas descriptivas y gráficos — un CPL en un panel no autoriza nada.

La conversión al formato que espera Meta (centavos enteros) es un paso explícito
en el borde de la integración, no una representación interna.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Optional

# Dos decimales: es la resolución de PEN, USD y de las monedas en las que opera
# el producto. Meta trabaja en unidades mínimas enteras, así que más precisión no
# tendría dónde aterrizar.
CENTIMOS = Decimal("0.01")


class MoneyConversionError(ValueError):
    """Un valor que debía ser dinero no se pudo interpretar como tal.

    Se levanta en vez de caer a cero: un cero silencioso en una compuerta
    financiera la deja pasar todo.
    """


def to_money(value: Any, *, field: str = "monto") -> Decimal:
    """Convierte a `Decimal` con dos decimales, sin pasar por `float`.

    El paso por `str` es lo que evita el error: `Decimal(0.1)` arrastra el
    residuo binario del float, `Decimal("0.1")` no. Por eso un `float` de
    entrada se serializa con `repr` antes de convertirse — es lo mejor que se
    puede hacer con un valor que ya perdió precisión, y deja de perder más.
    """
    if isinstance(value, Decimal):
        bruto = value
    elif isinstance(value, bool):
        raise MoneyConversionError(f"{field}: un booleano no es un monto.")
    elif isinstance(value, int):
        bruto = Decimal(value)
    elif isinstance(value, float):
        bruto = Decimal(repr(value))
    elif isinstance(value, str) and value.strip():
        try:
            bruto = Decimal(value.strip())
        except InvalidOperation as e:
            raise MoneyConversionError(f"{field}: '{value}' no es un monto válido.") from e
    else:
        raise MoneyConversionError(f"{field}: falta el monto o no es un número.")

    if not bruto.is_finite():
        raise MoneyConversionError(f"{field}: el monto no es finito.")
    return bruto.quantize(CENTIMOS, rounding=ROUND_HALF_UP)


def money_or_zero(value: Any) -> Decimal:
    """Como `to_money`, pero un valor ausente vale cero.

    Solo para AGREGAR gasto ya ocurrido —sumar snapshots donde una columna puede
    ser NULL—, nunca para el monto que se está autorizando: ahí la ausencia es
    un error, no un cero.
    """
    if value is None:
        return Decimal("0.00")
    try:
        return to_money(value)
    except MoneyConversionError:
        return Decimal("0.00")


def to_float(value: Optional[Decimal]) -> Optional[float]:
    """Para respuestas JSON y para columnas que siguen siendo `Float`.

    La pérdida de precisión ocurre acá, en el borde de salida, y ya no puede
    influir en ninguna decisión.
    """
    return None if value is None else float(value)


def to_minor_units(value: Decimal) -> int:
    """Al formato de Meta: unidades mínimas enteras (centavos).

    Único punto de conversión hacia la integración. Que sea explícito es lo que
    impide que el redondeo se cuele en un cálculo intermedio.
    """
    return int((to_money(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))
