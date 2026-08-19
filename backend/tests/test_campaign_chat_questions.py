"""Q&A del chat sobre los datos de la marca (CH-6).

Dos garantías que se prueban acá:
1. Las preguntas soportadas se responden con cifras REALES de `get_report` —
   nunca redactadas por un modelo (regla AI-1).
2. Una pregunta que ningún regex reconoce pasa por el LLM (que solo elige una
   etiqueta del catálogo cerrado) y recién ahí se rechaza; ni las preguntas ni
   los rechazos mueven el estado ni los slots de la sesión. Bajo MOCK_SERVICES
   —lo que corre en CI— no hay LLM y el rechazo es inmediato.
"""
import types
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.campaign_chat import questions
from app.services.campaign_chat.extraction import extract_message
from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401
from tests.test_campaign_chat_gates import _create_session_at_review


def _turn_id() -> str:
    return str(uuid.uuid4())


def _acc():
    """Stub mínimo de AdAccount: las fuentes de datos están mockeadas, solo
    hace falta el `.id` con el que se consultan los KPIs."""
    return types.SimpleNamespace(id="acc-1")


# --- Clasificación (pura, sin BD) ---

@pytest.mark.parametrize("text,expected", [
    ("¿cuánto gasté esta semana?", "SPEND"),
    ("cuanto llevo gastado", "SPEND"),
    ("¿cuántos leads tengo?", "LEADS"),
    ("cuantas conversaciones trajimos", "LEADS"),
    ("¿cuál es mi CPL?", "CPL"),
    ("cuanto me cuesta cada lead", "CPL"),
    ("¿cuál es mi mejor campaña?", "BEST_CAMPAIGN"),
    ("que campana me rinde mejor", "BEST_CAMPAIGN"),
    ("¿cuál campaña va mal?", "WORST_CAMPAIGN"),
    ("¿cómo me va?", "SUMMARY"),
    ("dame un resumen", "SUMMARY"),
    ("¿hay algún problema?", "ALERTS"),
    ("¿qué me recomendás?", "RECOMMENDATIONS"),
    ("¿cuánto debería invertir?", "BUDGET_ADVICE"),
    ("¿cuál es mi CTR?", "CTR"),
    ("¿cuántas campañas tengo?", "CAMPAIGNS_OVERVIEW"),
    # Singular: la forma natural de preguntarlo cuando hay una sola corriendo.
    # Caso real reportado 13-ago-2026 — antes caía en "fuera de tema".
    ("¿cuál es la campaña activa ahora?", "CAMPAIGNS_OVERVIEW"),
    ("que campana esta corriendo", "CAMPAIGNS_OVERVIEW"),
    ("¿cuáles campañas están activas?", "CAMPAIGNS_OVERVIEW"),
    ("¿a qué público le llega?", "AUDIENCE"),
    ("¿qué whatsapp uso?", "DESTINATION"),
    ("¿cómo funciona esto?", "HOW_IT_WORKS"),
    ("¿por qué queda pausada?", "WHY_PAUSED"),
])
def test_classify_supported_questions(text, expected):
    assert questions.classify_question(text) == expected


@pytest.mark.parametrize("text", [
    "¿cuánto está el dólar hoy?",
    "¿quién ganó el partido?",
    "¿me contás un chiste?",
    "¿cuál es la capital de Perú?",
])
def test_off_topic_questions_are_not_classified(text):
    assert questions.classify_question(text) is None
    assert questions.looks_like_question(text) is True


def test_a_goal_is_not_mistaken_for_a_question():
    assert questions.classify_question("quiero vender más zapatillas") is None
    assert questions.looks_like_question("quiero vender más zapatillas") is False


# --- Enrutado en el extractor ---

def test_supported_question_wins_over_slot_parsing_at_a_gate():
    # "¿cuánto gasté?" NO debe leerse como un cambio de presupuesto.
    result = extract_message("¿cuánto gasté?", state="REVIEW_PLAN", is_gate=True)
    assert result.intent == "question"
    assert result.question_topic == "SPEND"
    assert result.slots == {}


def test_off_topic_question_at_a_gate_is_rejected_deterministically():
    result = extract_message("¿cuánto está el dólar hoy?", state="REVIEW_PLAN", is_gate=True)
    assert result.intent == "off_topic"


def test_question_shaped_goal_is_still_captured_as_the_goal():
    # En ASK_GOAL nunca se rechaza por "fuera de tema": la frase es el objetivo.
    result = extract_message("¿me ayudás a vender zapatillas?", state="ASK_GOAL", is_gate=False)
    assert result.intent == "provide_info"
    # El pedido dirigido al asistente ("me ayudás a vender") no contamina el
    # nombre técnico del producto.
    assert result.slots["product_name"] == "Zapatillas"


def test_supported_question_answered_even_at_ask_goal():
    result = extract_message("¿cuánto gasté?", state="ASK_GOAL", is_gate=False)
    assert result.intent == "question"
    assert result.question_topic == "SPEND"


# --- Extremo a extremo por HTTP ---

@pytest.mark.asyncio
async def test_question_at_a_gate_answers_without_losing_the_card(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-q-gate"
    )
    before = (await async_client.get(
        f"/api/v1/campaigns/chat/sessions/{session_id}", headers=headers
    )).json()

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "¿cuánto gasté esta semana?"},
        headers=headers,
    )
    assert res.status_code == 200
    session = res.json()

    # Ni el estado ni los slots se movieron: la compuerta sigue pendiente.
    assert session["state"] == before["state"] == "REVIEW_PLAN"
    assert session["slots"] == before["slots"]
    assert session["draft_id"] is None

    # Respondió la pregunta (esta cuenta de test no tiene snapshots, así que la
    # respuesta honesta es "no tengo datos" — lo que importa es que NO es el
    # "no te entendí" ni el rechazo por fuera de tema).
    respuesta = session["messages"][-2]["text"]
    assert respuesta == questions.NO_DATA_TEXT
    assert "no te entendí" not in respuesta.lower()
    # Y se vuelve a plantear lo que estaba pendiente.
    assert "tarjeta" in session["messages"][-1]["text"].lower()


@pytest.mark.asyncio
async def test_off_topic_question_is_refused_and_state_is_untouched(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-q-offtopic"
    )
    before = (await async_client.get(
        f"/api/v1/campaigns/chat/sessions/{session_id}", headers=headers
    )).json()

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "¿cuánto está el dólar hoy?"},
        headers=headers,
    )
    session = res.json()
    assert session["state"] == before["state"]
    assert session["slots"] == before["slots"]
    assert "solo sé de tus campañas" in session["messages"][-2]["text"].lower()


@pytest.mark.asyncio
async def test_product_faq_is_answered_before_any_data_exists(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-q-faq"
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )).json()["id"]

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "¿por qué queda pausada?"},
        headers=headers,
    )
    session = res.json()
    assert "PAUSADA" in session["messages"][-2]["text"]
    # Sigue esperando el objetivo — la pregunta no consumió el turno del flujo.
    assert session["state"] == "ASK_GOAL"
    assert not session["slots"].get("goal_text")


@pytest.mark.asyncio
async def test_destination_question_uses_the_sessions_own_page(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-q-dest"
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )).json()["id"]

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "¿a qué whatsapp me escriben?"},
        headers=headers,
    )
    texto = res.json()["messages"][-2]["text"]
    assert "Mi Página" in texto
    assert "51999888777" in texto


@pytest.mark.asyncio
async def test_questions_never_call_the_llm(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """El punto de todo esto: una pregunta (soportada o no) cuesta 0 tokens."""
    spy = mocker.patch(
        "app.services.campaign_chat.engine._try_llm_extraction", autospec=True, return_value=None
    )
    headers, org_id, session_id, review_card = await _create_session_at_review(
        async_client, db_session, "chat-q-notokens"
    )

    for texto in ["¿cuánto gasté?", "¿cuál es mi mejor campaña?", "¿cuánto está el dólar?"]:
        await async_client.post(
            f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
            json={"client_turn_id": _turn_id(), "text": texto},
            headers=headers,
        )

    assert spy.call_count == 0


# --- Respuestas con datos: se prueban contra un reporte sintético para poder
# fijar las cifras exactas que debe decir el chat (el reporte real depende de
# lo que haya sincronizado, y acá lo que se verifica es la redacción/aritmética).

def _fake_report(**overrides):
    report = {
        "window_days": 7,
        "account": {"name": "Acc", "external_account_id": "act_1", "spend_cap": 1000.0,
                    "amount_spent": 400.0, "status": "ACTIVE"},
        "kpis": {
            "total_spend": 300.0, "total_reach": 12000, "total_conversations": 20,
            "avg_ctr": 1.85, "best_cost_per_conv": 12.5, "target_cpl": 15.0,
            "deltas_pct": {"spend": 10.0, "conversations": -5.0, "cost_per_conv": 8.0},
        },
        "campaigns": [
            {"campaign_id": "c1", "name": "CAMP_BUENA", "status": "ACTIVE", "spend": 150.0,
             "conversations": 12, "cost_per_conv": 12.5},
            {"campaign_id": "c2", "name": "CAMP_CARA", "status": "ACTIVE", "spend": 100.0,
             "conversations": 8, "cost_per_conv": 12.5},
            {"campaign_id": "c3", "name": "CAMP_SIN_RESULTADOS", "status": "PAUSED", "spend": 50.0,
             "conversations": 0, "cost_per_conv": 0.0},
        ],
        "segmentation": {
            "age": {"25-34": {"spend": 100.0, "conversions": 10},
                    "45-54": {"spend": 100.0, "conversions": 2}},
            "device": {"mobile": {"spend": 200.0, "conversions": 18}},
            "region": {}, "placements": {}, "gender": {},
        },
        "alerts": {"red": [{"name": "CAMP_SIN_RESULTADOS", "reason": "gastó sin conversaciones"}], "green": []},
        "findings": ["La campaña más eficiente es CAMP_BUENA"],
        "recommendations": ["Excluir instagram_positions"],
    }
    report.update(overrides)
    return report


def _fake_kpis(spend=300.0, conversations=20, cost_per_conv=15.0, delta_spend=10.0):
    return {
        "current": {"spend": spend, "conversations": conversations, "cost_per_conv": cost_per_conv},
        "previous": {"spend": 272.7, "conversations": 21, "cost_per_conv": 13.0},
        "deltas_pct": {"spend": delta_spend, "conversations": -5.0, "cost_per_conv": 8.0},
    }


@pytest.fixture
def _mock_report(mocker):
    """Mockea las dos fuentes de cifras del chat: `get_kpis_with_deltas` (la
    del dashboard, para gasto/leads/CPL) y `get_report` (ranking, alertas,
    segmentación)."""
    def _apply(report, kpis=None):
        for mod in ("questions", "facts"):
            mocker.patch(
                f"app.services.campaign_chat.{mod}.get_report",
                autospec=True, return_value=report,
            )
            mocker.patch(
                f"app.services.campaign_chat.{mod}.days_of_history",
                autospec=True, return_value=365,
            )
            mocker.patch(
                f"app.services.campaign_chat.{mod}.get_kpis_with_deltas",
                autospec=True, return_value=kpis if kpis is not None else _fake_kpis(),
            )
    return _apply


async def _answer(*args, **kwargs) -> str:
    """`answer_question` devuelve `(texto, facts)`; estos tests miran el texto.
    Los `facts` tienen su propia batería en test_campaign_chat_analysis.py."""
    text, _ = await questions.answer_question(*args, **kwargs)
    return text


@pytest.mark.asyncio
async def test_answer_spend_uses_real_kpis(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "SPEND", {})
    assert "S/. 300.00" in out
    assert "+10.0%" in out


@pytest.mark.asyncio
async def test_answer_spend_cap_uses_real_account_data(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "SPEND_CAP", {})
    assert "S/. 1000.00" in out
    assert "S/. 600.00" in out  # tope 1000 - gastado 400 = restante


@pytest.mark.asyncio
async def test_answer_leads_computes_average_cost(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "LEADS", {})
    assert "20 conversaciones" in out
    assert "S/. 15.00" in out  # 300 / 20


@pytest.mark.asyncio
async def test_answer_cpl_reports_average_and_target(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "CPL", {})
    assert "S/. 15.00" in out and "objetivo" in out


@pytest.mark.asyncio
async def test_answer_cpl_without_conversations_says_so(_mock_report):
    _mock_report(_fake_report(), kpis=_fake_kpis(spend=50.0, conversations=0, cost_per_conv=50.0))
    out = await _answer(None, "org", _acc(), "CPL", {})
    assert "no hay un costo por lead" in out.lower()


@pytest.mark.asyncio
async def test_answer_best_campaign_picks_the_first_with_results(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "BEST_CAMPAIGN", {})
    assert "CAMP_BUENA" in out and "12 conversaciones" in out


@pytest.mark.asyncio
async def test_answer_worst_campaign_prefers_spend_without_results(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "WORST_CAMPAIGN", {})
    assert "CAMP_SIN_RESULTADOS" in out
    assert "no trajo ninguna conversación" in out


@pytest.mark.asyncio
async def test_answer_campaigns_overview_counts_active(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "CAMPAIGNS_OVERVIEW", {})
    assert "3 campañas" in out and "2 activas" in out
    assert "CAMP_BUENA" in out


@pytest.mark.asyncio
async def test_answer_alerts_lists_red_ones(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "ALERTS", {})
    assert "CAMP_SIN_RESULTADOS" in out and "gastó sin conversaciones" in out


@pytest.mark.asyncio
async def test_answer_alerts_when_everything_is_fine(_mock_report):
    _mock_report(_fake_report(alerts={"red": [], "green": []}))
    out = await _answer(None, "org", _acc(), "ALERTS", {})
    assert "nada preocupante" in out


@pytest.mark.asyncio
async def test_answer_recommendations_and_summary(_mock_report):
    _mock_report(_fake_report())
    recs = await _answer(None, "org", _acc(), "RECOMMENDATIONS", {})
    assert "Excluir instagram_positions" in recs
    resumen = await _answer(None, "org", _acc(), "SUMMARY", {})
    assert "S/. 300.00" in resumen and "CAMP_BUENA" in resumen


@pytest.mark.asyncio
async def test_answer_budget_advice_is_20x_the_best_cost(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "BUDGET_ADVICE", {})
    assert "S/. 12.50" in out and "S/. 250.00" in out  # 12.50 * 20


@pytest.mark.asyncio
async def test_answer_audience_picks_cheapest_segment(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "AUDIENCE", {})
    assert "25-34" in out and "mobile" in out
    assert "45-54" not in out  # más caro, no se recomienda


@pytest.mark.asyncio
async def test_answer_ctr(_mock_report):
    _mock_report(_fake_report())
    out = await _answer(None, "org", _acc(), "CTR", {})
    assert "1.85%" in out


@pytest.mark.asyncio
async def test_empty_report_says_no_data(_mock_report):
    _mock_report({"campaigns": [], "kpis": {}}, kpis=_fake_kpis(spend=0, conversations=0, cost_per_conv=0))
    out = await _answer(None, "org", _acc(), "SPEND", {})
    assert out == questions.NO_DATA_TEXT


@pytest.mark.asyncio
async def test_unknown_topic_falls_back_to_out_of_scope(_mock_report):
    out = await _answer(None, "org", _acc(), "NO_EXISTE", {})
    assert out == questions.OUT_OF_SCOPE_TEXT


def test_off_topic_question_at_ask_goal_is_rejected_not_saved_as_the_goal():
    # Regresión: "¿cuánto está el dólar hoy?" terminaba guardado como el nombre
    # del producto solo porque el estado era ASK_GOAL.
    result = extract_message("¿cuánto está el dólar hoy?", state="ASK_GOAL", is_gate=False)
    assert result.intent == "off_topic"
    assert result.slots == {}


@pytest.mark.parametrize("text", [
    "¿me ayudás a vender zapatillas?",
    "¿podés promocionar mi restaurante?",
    "¿me armás algo para anunciar el taller?",
])
def test_a_goal_phrased_as_a_question_still_counts_as_the_goal(text):
    result = extract_message(text, state="ASK_GOAL", is_gate=False)
    assert result.intent == "provide_info"
    assert result.slots.get("goal_text") == text


def test_llm_catalog_matches_the_topics_the_code_can_answer():
    """El catálogo que se le pasa al LLM debe ser exactamente el conjunto de
    temas respondibles: una etiqueta de más haría gastar la llamada para
    terminar en OUT_OF_SCOPE, y una de menos sería un tema que el LLM nunca
    podría elegir aunque el regex sí lo reconozca."""
    from app.services.campaign_chat.questions import (
        _KPI_ANSWERS, _REPORT_ANSWERS, _TOPIC_PATTERNS, TOPIC_DESCRIPTIONS,
    )
    assert {t for t, _ in _TOPIC_PATTERNS} == set(TOPIC_DESCRIPTIONS)
    estaticos = {"HOW_IT_WORKS", "WHY_PAUSED", "DESTINATION"}
    assert set(TOPIC_DESCRIPTIONS) == estaticos | set(_KPI_ANSWERS) | set(_REPORT_ANSWERS)


@pytest.mark.asyncio
async def test_h4_reach_question_disambiguated_from_leads(_mock_report):
    """H4: '¿a cuánta gente le llegué?' debe clasificar como REACH y responder alcance, no leads."""
    assert questions.classify_question("¿A cuánta gente le llegué en los últimos 30 días?") == "REACH"
    _mock_report({"campaigns": [], "kpis": {"total_reach": 15400, "total_impressions": 30800, "avg_ctr": 2.1}})
    out = await _answer(None, "org", _acc(), "REACH", {})
    assert "15.400 personas" in out
    assert "30.800 impresiones" in out
    assert "conversaciones" not in out


@pytest.mark.asyncio
async def test_h4_spend_without_spend_cap_is_clean(_mock_report):
    """H4: '¿cuánto gasté?' en cuenta sin spend_cap no menciona topes de gasto."""
    acc_no_cap = types.SimpleNamespace(id="acc_no_cap", name="Cuenta Sin Tope", spend_cap=0.0, amount_spent=250.0, status="ACTIVE")
    _mock_report(_fake_report())
    out = await _answer(None, "org", acc_no_cap, "SPEND", {})
    assert "S/. 300.00" in out
    assert "tope" not in out.lower()
    assert "restante" not in out.lower()
