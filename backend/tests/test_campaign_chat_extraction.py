"""Tests del extractor determinista del chat de creación de campañas (CH-1).
Puros: sin BD, sin fixtures async — corren instantáneo y son la base de la
cobertura del feature nuevo."""
import pytest

from app.services.campaign_chat.extraction import (
    extract_message, parse_budget, parse_budget_type, parse_days, parse_whatsapp,
)
from app.services.campaign_chat.slots import sanitize_product_name, strip_accents


@pytest.mark.parametrize("text,expected", [
    ("S/. 300", 300.0),
    ("300 soles", 300.0),
    ("s/300", 300.0),
    ("$500", 500.0),
    ("1.200,50 soles", 1200.5),
    ("1,200.50 soles", 1200.5),
    ("1200 soles", 1200.0),
    ("no hay presupuesto", None),
])
def test_parse_budget(text, expected):
    assert parse_budget(strip_accents(text).lower()) == expected


@pytest.mark.parametrize("text,expected", [
    ("10 dias", 10),
    ("10 días", 10),
    ("una semana", 7),
    ("1 semana", 7),
    ("dos semanas", 14),
    ("un mes", 30),
    ("sin duracion", None),
])
def test_parse_days(text, expected):
    assert parse_days(strip_accents(text).lower()) == expected


@pytest.mark.parametrize("text,expected", [
    ("por dia", "DAILY"),
    ("diario", "DAILY"),
    ("al dia", "DAILY"),
    ("en total", "LIFETIME"),
    ("sin mencion", None),
])
def test_parse_budget_type(text, expected):
    assert parse_budget_type(strip_accents(text).lower()) == expected


def test_parse_whatsapp():
    assert parse_whatsapp("mi numero es +51 999 888 777") == "51999888777"
    assert parse_whatsapp("999888777") == "999888777"


@pytest.mark.parametrize("text,expected", [
    ("Cerámicas Piñero 🔥", "Ceramicas Pinero"),
    ("quiero vender más zapatillas para hombre", "Zapatillas Hombre"),
    ("", "Producto"),
    ("   ", "Producto"),
])
def test_sanitize_product_name(text, expected):
    assert sanitize_product_name(text) == expected


@pytest.mark.parametrize("text", ["si", "sí", "dale", "ok", "listo", "perfecto", "de una", "confirmo"])
def test_accept_phrases(text):
    result = extract_message(text, state="REVIEW_PLAN", is_gate=True)
    assert result.intent == "accept"


@pytest.mark.parametrize("text", ["no", "mejor no", "para", "rechazo"])
def test_reject_phrases(text):
    result = extract_message(text, state="REVIEW_PLAN", is_gate=True)
    assert result.intent == "reject"


def test_no_se_si_si_is_not_accept():
    result = extract_message("no se si si", state="REVIEW_PLAN", is_gate=True)
    assert result.intent != "accept"


@pytest.mark.parametrize("text", ["cancelar", "cancela esto", "olvidalo", "ya no quiero"])
def test_cancel_phrases_work_in_any_state(text):
    for state in ["ASK_GOAL", "ASK_MEDIA", "REVIEW_PLAN", "CONFIRM_RISK"]:
        result = extract_message(text, state=state, is_gate=(state in {"REVIEW_PLAN", "CONFIRM_RISK"}))
        assert result.intent == "cancel", f"failed for state={state}"


def test_goal_text_captures_free_text_and_product_name():
    result = extract_message("quiero vender más zapatillas para hombre", state="ASK_GOAL", is_gate=False)
    assert result.intent == "provide_info"
    assert result.slots["goal_text"] == "quiero vender más zapatillas para hombre"
    assert result.slots["product_name"] == "Zapatillas Hombre"


def test_change_budget_and_days_in_one_message():
    result = extract_message("500 soles por 10 dias", state="CONFIRM_RISK", is_gate=True)
    assert result.intent in {"change", "accept"}  # nunca debe leerse como "accept"
    assert result.intent == "change"
    assert result.slots["budget"] == 500.0
    assert result.slots["days"] == 10


def test_change_hint_outside_gate_still_detected():
    result = extract_message("mejor sube el presupuesto a 400", state="REVIEW_PLAN", is_gate=False)
    assert result.intent == "change"
    assert result.slots["budget"] == 400.0


def test_off_topic_question_is_rejected_without_reaching_the_llm():
    # (CH-6) Antes esto caía en "unclear", que dispara una llamada al LLM para
    # intentar entenderlo. Una pregunta fuera de tema ahora se rechaza acá
    # mismo: es determinista y cuesta 0 tokens.
    result = extract_message("¿cuánto está el dólar hoy?", state="CONFIRM_RISK", is_gate=True)
    assert result.intent == "off_topic"


def test_unclear_when_nothing_matches_and_it_is_not_a_question():
    result = extract_message("azul verde amarillo", state="CONFIRM_RISK", is_gate=True)
    assert result.intent == "unclear"


def test_empty_text_is_unclear():
    assert extract_message("", state="ASK_GOAL", is_gate=False).intent == "unclear"
