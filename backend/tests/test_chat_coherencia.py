"""Coherencia de la conversación del chat.

Los tres bugs de acá salieron de una conversación real en producción, y los
tres tenían la misma forma: cada respuesta era correcta por separado y el
diálogo no cerraba.
"""
import pytest

from app.services.campaign_chat.questions import (
    OUT_OF_SCOPE_TEXT, _campaigns_with_results, classify_question_topics,
)


# --- 1. "¿Hasta cuándo llega la información?" contestada con fechas de campaña ---

def test_cobertura_no_arrastra_las_fechas_de_campana():
    """El chat contestó "creada el 2024-09-25, sigue activa (695 días
    corriendo)" a alguien que preguntaba hasta qué día había datos.

    COVERAGE y SCHEDULE matcheaban los dos: no son dos cosas que alguien quiera
    saber a la vez, son dos LECTURAS de la misma frase.
    """
    for pregunta in [
        "HASTA CUÁNDO LLEGA LA INFORMACIÓN",
        "hasta cuándo llega la información",
        "hasta que fecha tienes datos",
        "desde cuando tenes datos",
    ]:
        assert classify_question_topics(pregunta) == ["COVERAGE"], pregunta


def test_las_preguntas_de_campana_siguen_yendo_a_schedule():
    assert classify_question_topics("cuando fue creada la campana?") == ["SCHEDULE"]


def test_las_compuestas_legitimas_siguen_funcionando():
    """Excluir temas no puede romper "cuánto gasté y a cuánta gente alcancé",
    que sí son dos preguntas de verdad."""
    temas = classify_question_topics("cuanto gaste y cuanta gente alcance?")
    assert "SPEND" in temas and "REACH" in temas


def test_una_pregunta_de_ventas_no_arrastra_el_gasto():
    """"¿cuánto vendí?" no puede terminar respondiendo con la INVERSIÓN."""
    assert classify_question_topics("cuanto vendi este mes?") == ["NOT_MEASURED"]


# --- 2. "Tu mejor campaña ... a S/. 0.00 por contacto" ---

def _reporte(campanas):
    return {"campaigns": campanas}


def test_una_campana_sin_gasto_no_puede_ser_la_mejor():
    """`cost_per_conv = spend / resultados`, así que gasto 0 da costo 0 y —al
    ordenar de menor a mayor— queda PRIMERA. El chat llegó a contestar "tu
    mejor campaña ... a S/. 0.00 por contacto": un número imposible presentado
    como un logro.

    Gasto 0 con resultados pasa de verdad (atribución en un día cuyo gasto
    todavía no sincronizó). No es una campaña gratis, es un dato incompleto.
    """
    rankeables = _campaigns_with_results(_reporte([
        {"name": "Fantasma", "conversations": 4, "spend": 0.0, "cost_per_conv": 0.0},
        {"name": "Real", "conversations": 20, "spend": 250.0, "cost_per_conv": 12.5},
    ]))
    assert [c["name"] for c in rankeables] == ["Real"]


def test_una_campana_sin_resultados_tampoco():
    rankeables = _campaigns_with_results(_reporte([
        {"name": "Sin resultados", "conversations": 0, "spend": 500.0, "cost_per_conv": 500.0},
    ]))
    assert rankeables == []


def test_sin_ninguna_rankeable_no_se_inventa_una():
    assert _campaigns_with_results(_reporte([])) == []


# --- 3. "Solo sé de tus campañas de Meta Ads" ante una pregunta de campañas ---

def test_el_texto_de_fuera_de_tema_no_se_contradice():
    """Salía también cuando la pregunta ERA sobre las campañas — por ejemplo
    "quién creó las últimas campañas". Negar conocer el tema del que uno mismo
    habla es lo que hace que la conversación no cierre."""
    assert "solo sé de tus campañas" not in OUT_OF_SCOPE_TEXT.lower()
    # Dice qué SÍ puede, e invita a reformular en vez de cerrar la puerta.
    assert "sí puedo" in OUT_OF_SCOPE_TEXT.lower()
    assert "reformul" in OUT_OF_SCOPE_TEXT.lower()


# --- 4. La repregunta llega sin referente ---

def test_el_ultimo_intercambio_se_extrae_del_historial():
    """"¿cómo que 0 por contacto?" no se puede clasificar mirándola sola: el
    planificador necesita el turno anterior para resolver la referencia."""
    from app.services.campaign_chat.engine import _ultimo_intercambio

    historial = [
        {"type": "text", "role": "assistant", "text": "¡Hola!"},
        {"type": "text", "role": "user", "text": "cual rinde mejor"},
        {"type": "text", "role": "assistant", "text": "Tu mejor campaña es X a S/. 12.50."},
    ]
    assert _ultimo_intercambio(historial) == (
        "cual rinde mejor", "Tu mejor campaña es X a S/. 12.50."
    )


def test_sin_conversacion_previa_no_inventa_contexto():
    from app.services.campaign_chat.engine import _ultimo_intercambio

    assert _ultimo_intercambio([]) is None
    assert _ultimo_intercambio([
        {"type": "text", "role": "assistant", "text": "¡Hola!"},
    ]) is None


def test_las_tarjetas_no_cuentan_como_intercambio():
    """Una tarjeta de revisión no es una respuesta a una pregunta."""
    from app.services.campaign_chat.engine import _ultimo_intercambio

    assert _ultimo_intercambio([
        {"type": "review_card", "role": "assistant", "card_id": "c1"},
        {"type": "text", "role": "user", "text": "dale"},
    ]) is None
