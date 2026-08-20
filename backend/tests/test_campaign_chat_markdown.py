"""Normalización de markdown de las respuestas del chat (punto 3 del plan de
arreglos): una lista sin línea en blanco antes del párrafo siguiente se pega
visualmente al último ítem en `react-markdown` (CommonMark la trata como
continuación perezosa del mismo párrafo)."""
from app.services.campaign_chat.cards import text_message, user_text_message
from app.services.campaign_chat.markdown_utils import normalize_markdown


def test_inserts_blank_line_between_list_and_following_paragraph():
    text = (
        "Tus campañas:\n"
        "• JMM086-SAP MM-CP-JUL26\n"
        "Si querés, también te puedo decir cuáles están activas y cuáles están pausadas."
    )
    normalized = normalize_markdown(text)
    assert normalized == (
        "Tus campañas:\n"
        "• JMM086-SAP MM-CP-JUL26\n"
        "\n"
        "Si querés, también te puedo decir cuáles están activas y cuáles están pausadas."
    )


def test_leaves_well_formed_text_untouched():
    text = "Línea 1\n• Ítem 1\n• Ítem 2\n\nPárrafo final."
    assert normalize_markdown(text) == text


def test_numbered_list_also_gets_the_blank_line():
    text = "Pasos:\n1. Primero\n2. Segundo\nListo, eso es todo."
    normalized = normalize_markdown(text)
    assert normalized == "Pasos:\n1. Primero\n2. Segundo\n\nListo, eso es todo."


def test_empty_and_single_line_text_are_unaffected():
    assert normalize_markdown("") == ""
    assert normalize_markdown("una sola línea") == "una sola línea"


def test_text_message_normalizes_assistant_text_but_not_user_text():
    broken = "• Ítem único\nsigue el párrafo pegado."
    assistant_msg = text_message(broken)
    assert assistant_msg["text"] == "• Ítem único\n\nsigue el párrafo pegado."

    user_msg = user_text_message(broken)
    assert user_msg["text"] == broken
