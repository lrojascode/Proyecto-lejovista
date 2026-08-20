"""Punto 5 de fase 06: el chat habla un solo formato de moneda.

Estos tests existen porque el formato ya derivó una vez: había dos copias de
`_money` y al arreglar una sola, el mismo monto salía `S/. 7.417,19` en una
respuesta y `S/.7417.19` en la siguiente.
"""
from app.services.campaign_chat import cards, facts, formatting, questions
from app.services.campaign_chat.grounding import numbers_are_grounded


def test_hay_una_sola_definicion_de_formato_en_el_chat():
    """Cualquier módulo del chat que formatee plata usa la MISMA función."""
    assert facts._money is formatting.money
    assert questions._money is formatting.money
    assert facts._fmt_int is formatting.fmt_int
    assert questions._fmt_int is formatting.fmt_int


def test_convencion_es_pe():
    assert formatting.money(7417.19) == "S/. 7.417,19"
    assert formatting.money(1000) == "S/. 1.000,00"
    assert formatting.money(0) == "S/. 0,00"
    assert formatting.money(-1234.5) == "S/. -1.234,50"
    assert formatting.money(None) == "S/. 0,00"
    assert formatting.fmt_int(789412) == "789.412"
    assert formatting.fmt_int(None) == "0"


def test_la_tarjeta_de_aprobacion_usa_el_formato_del_chat():
    """`build_review_card` es la pantalla donde el usuario confirma gasto real:
    no puede mostrar el monto distinto a como se lo dijo el análisis."""
    card = cards.build_review_card(
        {"product_name": "Curso", "budget": 1000.0, "budget_type": "DAILY", "days": 7},
        {"suggested_campaign_name": "CAMP-1", "copy": {}},
    )
    budget = next(f for f in card["fields"] if f["key"] == "budget")
    assert budget["value"] == "S/. 1.000,00 (por día)"


def test_el_formato_sobrevive_al_grounding():
    """Un monto formateado tiene que seguir siendo verificable contra el fact
    numérico crudo, o la guarda descartaría la respuesta entera."""
    assert numbers_are_grounded({"kpis": {"spend": 7417.19}}, f"Llevás {formatting.money(7417.19)} invertidos.")
    assert numbers_are_grounded({"alcance": {"total_personas": 789412}}, f"Alcanzaste a {formatting.fmt_int(789412)} personas.")
    # Y una cifra que NO está en los facts se sigue rechazando.
    assert not numbers_are_grounded({"kpis": {"spend": 7417.19}}, "Llevás S/. 9.999,99 invertidos.")
