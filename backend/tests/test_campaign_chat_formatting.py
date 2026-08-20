"""Un solo formato de moneda, y el correcto para Perú.

Dos bugs distintos viven acá:

1. Había dos copias de `_money` y al arreglar una sola, el mismo monto salía de
   dos formas en mensajes consecutivos.
2. La copia "arreglada" agrupaba como `7.417,19`, que es la convención
   argentina/española. Perú agrupa con coma y separa decimales con punto.
"""
from app.services.campaign_chat import cards, facts, questions
from app.utils import formatting
from app.services.campaign_chat.grounding import numbers_are_grounded


def test_hay_una_sola_definicion_de_formato_en_el_chat():
    """Cualquier módulo del chat que formatee plata usa la MISMA función."""
    assert facts._money is formatting.money
    assert questions._money is formatting.money
    assert facts._fmt_int is formatting.fmt_int
    assert questions._fmt_int is formatting.fmt_int


def test_convencion_peruana_no_la_argentina():
    """Perú: miles con COMA, decimales con PUNTO. Es lo que dan los datos de
    locale es-PE, y lo contrario de es-AR / es-ES / es-CO."""
    assert formatting.money(7417.19) == "S/. 7,417.19"
    assert formatting.money(1000) == "S/. 1,000.00"
    assert formatting.money(0) == "S/. 0.00"
    assert formatting.money(-1234.5) == "S/. -1,234.50"
    assert formatting.money(None) == "S/. 0.00"
    assert formatting.fmt_int(789412) == "789,412"
    assert formatting.fmt_int(1234567) == "1,234,567"
    assert formatting.fmt_int(None) == "0"


def test_la_tarjeta_de_aprobacion_usa_el_formato_del_chat():
    """`build_review_card` es la pantalla donde el usuario confirma gasto real:
    no puede mostrar el monto distinto a como se lo dijo el análisis."""
    card = cards.build_review_card(
        {"product_name": "Curso", "budget": 1000.0, "budget_type": "DAILY", "days": 7},
        {"suggested_campaign_name": "CAMP-1", "copy": {}},
    )
    budget = next(f for f in card["fields"] if f["key"] == "budget")
    assert budget["value"] == "S/. 1,000.00 (por día)"


def test_el_formato_sobrevive_al_grounding():
    """Un monto formateado tiene que seguir siendo verificable contra el fact
    numérico crudo, o la guarda descartaría la respuesta entera."""
    assert numbers_are_grounded({"kpis": {"spend": 7417.19}}, f"Llevás {formatting.money(7417.19)} invertidos.")
    assert numbers_are_grounded({"alcance": {"total_personas": 789412}}, f"Alcanzaste a {formatting.fmt_int(789412)} personas.")
    # Y una cifra que NO está en los facts se sigue rechazando.
    assert not numbers_are_grounded({"kpis": {"spend": 7417.19}}, "Llevás S/. 9.999,99 invertidos.")
