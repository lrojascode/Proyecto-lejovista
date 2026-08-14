"""Control de seguridad de la reescritura conversacional (CH-7). Puro.

Es la pieza que sostiene la regla AI-1 cuando el LLM redacta: el modelo puede
cambiar el tono, pero si su versión tiene un número que no estaba —o le falta
uno que sí estaba— se descarta entera y el usuario ve el texto del código.
"""
import pytest

from app.services.campaign_chat.rephrase import (
    is_safe_rephrase, numeric_tokens, safe_or_canonical, url_tokens,
)

_CANONICO = "Llevás gastados S/. 1085.47 en los últimos 7 días, con 402 conversaciones."


def test_faithful_rephrase_is_accepted():
    bueno = "En estos 7 días gastaste S/. 1085.47 y ya juntaste 402 conversaciones."
    assert is_safe_rephrase(_CANONICO, bueno) is True


def test_invented_number_is_rejected():
    """El caso que esta función existe para atajar: el modelo agrega un CPL que
    nadie calculó."""
    inventado = "Gastaste S/. 1085.47 en 7 días, 402 conversaciones, o sea S/. 2.70 cada una."
    assert is_safe_rephrase(_CANONICO, inventado) is False


def test_dropped_number_is_rejected():
    """Se exige igualdad, no subconjunto: perder el dato por el que preguntaron
    es tan malo como inventar uno."""
    incompleto = "Llevás gastados S/. 1085.47 estos días y varias conversaciones más."
    assert is_safe_rephrase(_CANONICO, incompleto) is False


def test_changed_number_is_rejected():
    redondeado = "En estos 7 días gastaste S/. 1085 y ya juntaste 402 conversaciones."
    assert is_safe_rephrase(_CANONICO, redondeado) is False


def test_number_formatting_does_not_matter():
    """"1.085,47" y "1085.47" son el mismo número: cambiar el formato no puede
    costar una reescritura válida."""
    otro_formato = "En estos 7 días gastaste S/ 1.085,47 y juntaste 402 conversaciones."
    assert is_safe_rephrase(_CANONICO, otro_formato) is True


@pytest.mark.parametrize("mala", ["", "   ", "Listo."])
def test_empty_or_gutted_rephrase_is_rejected(mala):
    assert is_safe_rephrase(_CANONICO, mala) is False


def test_padded_rephrase_is_rejected():
    inflado = _CANONICO + " " + ("Y además te cuento un montón de cosas más sin datos. " * 4)
    assert is_safe_rephrase(_CANONICO, inflado) is False


def test_short_prompt_can_be_shortened():
    """El piso de longitud no se le aplica a los textos cortos: acortar una
    pregunta es exactamente lo que hace que suene a persona."""
    original = "¿Qué querés promocionar? Contámelo con tus palabras."
    assert is_safe_rephrase(original, "¿Qué querés promocionar?") is True


def test_links_must_survive_untouched():
    original = "¡Listo! Revisala acá: https://adsmanager.facebook.com/x?act=1"
    assert is_safe_rephrase(original, "¡Quedó! Mirala acá: https://adsmanager.facebook.com/x?act=1") is True
    assert is_safe_rephrase(original, "¡Quedó! Mirala en Ads Manager cuando quieras.") is False
    assert is_safe_rephrase(original, "¡Quedó! Entrá a https://otro-sitio.com/x?act=1 nomás") is False


def test_safe_or_canonical_falls_back():
    inventado = "Gastaste S/. 1085.47 en 7 días, 402 conversaciones, o sea S/. 2.70 cada una."
    assert safe_or_canonical(_CANONICO, inventado) == _CANONICO
    bueno = "En estos 7 días gastaste S/. 1085.47 y ya juntaste 402 conversaciones."
    assert safe_or_canonical(_CANONICO, bueno) == bueno


def test_token_helpers():
    assert numeric_tokens("S/. 1.085,47 en 7 días") == {"108547", "7"}
    assert url_tokens("mirá https://a.com/x y nada más") == {"https://a.com/x"}
