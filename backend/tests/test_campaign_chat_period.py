"""Detección del período de una pregunta (CH-8). Puro.

Antes toda respuesta hablaba de los últimos 7 días y no había forma de pedir
otra cosa. Acá se fija que el número salga siempre del texto de la persona o de
un catálogo cerrado — nunca de un modelo.
"""
import pytest

from app.services.campaign_chat.period import (
    MAX_HISTORY_DAYS, PERIOD_ENUM, Period, clamp_to_available, parse_period, period_from_enum,
)


@pytest.mark.parametrize("texto,dias", [
    ("¿cuánto gasté hoy?", 1),
    ("¿cómo me fue ayer?", 2),
    ("¿cuánto llevo esta semana?", 7),
    ("¿y la semana pasada?", 14),
    ("¿cómo me fue este mes?", 30),
    ("¿cuánto gasté el mes pasado?", 60),
    ("¿cómo viene el trimestre?", 90),
    ("dame el resumen de los últimos 45 días", 45),
    ("¿cuántos leads en las últimas 3 semanas?", 21),
    ("¿cuánto invertí en los últimos 2 meses?", 60),
    ("¿cuánto gasté desde que empecé?", MAX_HISTORY_DAYS),
    ("dame todo el historial", MAX_HISTORY_DAYS),
])
def test_parse_period(texto, dias):
    period = parse_period(texto)
    assert period is not None, texto
    assert period.days == dias


@pytest.mark.parametrize("texto", [
    "¿cuánto gasté?",
    "¿cuál es mi mejor campaña?",
    "quiero vender zapatillas",
    "",
])
def test_questions_without_a_period_return_none(texto):
    """Sin período mencionado, el motor usa la ventana por defecto (7 días,
    la misma del dashboard)."""
    assert parse_period(texto) is None


def test_explicit_period_is_capped_at_what_the_db_keeps():
    """Pedir 3 años no puede prometer 3 años: más allá del retention los
    snapshots se fueron a las tablas de archivo."""
    period = parse_period("dame los últimos 3 años")
    assert period.days == MAX_HISTORY_DAYS
    assert "historial" in period.label


def test_the_label_always_names_the_window():
    """La etiqueta viaja hasta la respuesta: un número sin decir de qué período
    habla es un número que confunde más de lo que informa."""
    assert parse_period("los últimos 45 días").label == "los últimos 45 días"
    assert parse_period("los últimos 1 dia").label == "los últimos 1 día"
    assert parse_period("este mes").label == "este mes"


def test_period_from_enum_only_accepts_the_catalog():
    """El LLM elige una ETIQUETA, no un entero. Cualquier cosa fuera del
    catálogo se descarta (regla AI-1: el modelo no propone cifras)."""
    assert period_from_enum("30d").days == 30
    assert period_from_enum("all").days == MAX_HISTORY_DAYS
    assert period_from_enum("37d") is None
    assert period_from_enum(37) is None
    assert period_from_enum(None) is None


def test_every_enum_entry_is_a_valid_period():
    for clave, period in PERIOD_ENUM.items():
        assert isinstance(period, Period) and period.days > 0, clave
        assert period.label.strip()


def test_clamp_admits_what_fits():
    period = Period(30, "este mes")
    assert clamp_to_available(period, 90) is period
    assert clamp_to_available(period, 30) is period


def test_clamp_shrinks_and_says_so():
    """Responder "en los últimos 90 días" con 10 días de datos daría por ceros
    80 días que nunca se midieron."""
    recortado = clamp_to_available(Period(90, "los últimos 90 días"), 10)
    assert recortado.days == 10
    assert "10 días" in recortado.label
    assert "todo lo que tengo sincronizado" in recortado.label


def test_clamp_without_any_data_keeps_the_request():
    """Sin snapshots no hay nada que recortar — la respuesta va a ser
    NO_DATA_TEXT igual."""
    period = Period(30, "este mes")
    assert clamp_to_available(period, 0) is period
