"""El analista es el camino por defecto, no la excepción.

En producción el chat devolvió seis respuestas seguidas y **ninguna** pasó por
el LLM con herramientas: dos fueron `OUT_OF_SCOPE`, dos el mismo texto repetido
y una "No te entendí bien".

La causa no era el modelo ni la guarda de números. Era que casi nunca se lo
llamaba: un regex clasificaba la pregunta primero, y lo que no reconocía moría
antes de que el modelo existiera. Las siete herramientas de consulta estaban
del otro lado de esa compuerta. `buscar_anuncios` contesta exactamente "¿qué
anuncios rinden mejor y qué dicen?", y esa pregunta terminaba en un menú de
disculpas.

Estos tests fijan el orden nuevo: el analista primero, el regex de respaldo.
"""
import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_chat_session import CampaignChatSession
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.services.campaign_chat import engine
from app.services.campaign_chat.questions import OUT_OF_SCOPE_TEXT

# Las seis del transcripto real, más los tres botones que el propio chat ofrece
# en el saludo (`engine.py`, quick_replies del modo ASK). Dos de esos tres
# botones caían en OUT_OF_SCOPE: el producto sugería preguntas que después se
# negaba a contestar.
PREGUNTAS_REALES = [
    "hasta que fecha tienes datos",
    "cuando se creo la cuenta de its?",
    "cuantas campanas estan activas ahora?",
    "dime las 13 campanas activas",
    "ya pero listamelas",
    "¿qué anuncios rinden mejor y qué dicen?",
    "¿Cómo vengo este mes?",
    "¿Cuál campaña rinde mejor?",
    "¿Dónde estoy perdiendo plata?",
]

RESPUESTA_DEL_ANALISTA = "Gastaste S/. 100.00 y trajiste 10 resultados."
CONSULTADO = [{"consulta": "resumen_cuenta", "argumentos": {},
               "resultado": {"gasto": 100.0, "resultados": 10}}]


@pytest_asyncio.fixture
async def sesion(db_session: AsyncSession):
    org = Organization(slug="analista", name="Analista", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()

    user = User(email="a@example.com", hashed_password="x", full_name="A")
    acc = AdAccount(organization_id=org.id, platform="META",
                    external_account_id="act_1", name="Acc", status="ACTIVE")
    db_session.add_all([user, acc])
    await db_session.flush()
    db_session.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    await db_session.flush()
    await db_session.refresh(user, ["organizations"])

    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="JMM086-SAP MM-CP-JUL26", status="ACTIVE",
        effective_status="ACTIVE", date=datetime.date.today() - datetime.timedelta(days=1),
        spend=100.0, impressions=1000, clicks=20, conversions=10,
        conversations=10, results=10,
    ))

    ses = CampaignChatSession(
        organization_id=org.id, created_by_user_id=user.id, ad_account_id=acc.id,
        state="BROWSING", status="OPEN", mode="ASK",
        slots_json={"_origin_mode": "ASK"}, messages_json=[], turn_count=0,
    )
    db_session.add(ses)
    await db_session.commit()
    return db_session, user, ses


@pytest.fixture
def analista(mocker):
    """Deja al analista disponible y registra con qué se lo llamó."""
    return mocker.patch(
        "app.services.ai.llm.OpenRouterClient.answer_with_tools",
        autospec=True,
        return_value=(RESPUESTA_DEL_ANALISTA, CONSULTADO),
    )


async def _preguntar(sesion, texto):
    db, user, ses = sesion
    mensajes = list(ses.messages_json or [])
    mensajes.append({"type": "text", "role": "user", "text": texto})
    await engine._handle_text(db, user, ses, texto, mensajes)
    ses.messages_json = mensajes
    return mensajes


def _textos(mensajes):
    return [m.get("text", "") for m in mensajes if m.get("role") == "assistant"]


# --- El test que hubiera atajado esto ---

@pytest.mark.parametrize("pregunta", PREGUNTAS_REALES)
@pytest.mark.asyncio
async def test_las_preguntas_reales_llegan_al_analista(sesion, analista, pregunta):
    """Las seis del transcripto y los tres botones del saludo.

    Cuatro de estas nueve clasificaban como `[]` y terminaban en
    `_handle_off_topic` → `OUT_OF_SCOPE_TEXT`, sin que las herramientas
    llegaran a correr.
    """
    mensajes = await _preguntar(sesion, pregunta)

    assert analista.called, f"el analista no se llamó para: {pregunta}"
    assert RESPUESTA_DEL_ANALISTA in _textos(mensajes)


@pytest.mark.parametrize("pregunta", PREGUNTAS_REALES)
@pytest.mark.asyncio
async def test_ninguna_pregunta_real_termina_en_out_of_scope(sesion, analista, pregunta):
    mensajes = await _preguntar(sesion, pregunta)
    assert OUT_OF_SCOPE_TEXT not in _textos(mensajes), pregunta


@pytest.mark.asyncio
async def test_una_repregunta_no_es_un_mensaje_incomprensible(sesion, analista):
    """"ya pero listamelas" caía en intent=unclear → "No te entendí bien.
    ¿Podés responder la pregunta de arriba?" — a alguien que estaba pidiendo
    algo perfectamente claro sobre la respuesta anterior."""
    mensajes = await _preguntar(sesion, "ya pero listamelas")
    assert not any("No te entendí" in t for t in _textos(mensajes))


# --- El hilo, que es lo que le da sentido a una repregunta ---

@pytest.mark.asyncio
async def test_el_analista_recibe_el_hilo_previo(sesion, analista):
    db, user, ses = sesion
    ses.messages_json = [
        {"type": "text", "role": "user", "text": "cuantas campanas estan activas?"},
        {"type": "text", "role": "assistant", "text": "Tenés 13 campañas activas."},
    ]
    await _preguntar(sesion, "ya pero listamelas")

    hilo = analista.call_args.kwargs["thread"]
    assert [t["role"] for t in hilo] == ["user", "assistant"]
    assert "13 campañas activas" in hilo[-1]["content"]


@pytest.mark.asyncio
async def test_la_pregunta_actual_no_se_duplica_en_el_hilo(sesion, analista):
    """`answer_with_tools` manda la pregunta por separado; si además viniera en
    el hilo, el modelo la vería dos veces."""
    await _preguntar(sesion, "cuanto gaste?")
    hilo = analista.call_args.kwargs["thread"]
    assert not any(t["content"] == "cuanto gaste?" for t in hilo)


def test_el_hilo_se_corta_en_los_ultimos_turnos():
    mensajes = []
    for i in range(20):
        mensajes.append({"type": "text", "role": "user", "text": f"p{i}"})
        mensajes.append({"type": "text", "role": "assistant", "text": f"r{i}"})
    hilo = engine._hilo_reciente(mensajes, turnos=6)
    assert len(hilo) == 12
    assert hilo[-1]["content"] == "r19"


def test_las_tarjetas_no_entran_al_hilo():
    """Sólo texto: una review_card o una risk_card no es conversación."""
    mensajes = [
        {"type": "review_card", "role": "assistant", "card_id": "x"},
        {"type": "text", "role": "assistant", "text": "hola"},
    ]
    assert engine._hilo_reciente(mensajes) == [{"role": "assistant", "content": "hola"}]


# --- Los límites que no se movieron ---

@pytest.mark.asyncio
async def test_sin_llm_todo_cae_al_camino_determinista(sesion, mocker):
    """El analista es el camino por defecto, no un requisito. Sin API key
    `answer_with_tools` devuelve None y las respuestas canónicas siguen
    saliendo — es lo que sostiene la suite entera."""
    mocker.patch(
        "app.services.ai.llm.OpenRouterClient.answer_with_tools",
        autospec=True, return_value=None,
    )
    mensajes = await _preguntar(sesion, "cuando se creo la cuenta de its?")
    assert _textos(mensajes), "sin analista tiene que responder algo igual"


@pytest.mark.asyncio
async def test_una_respuesta_con_numeros_inventados_se_descarta(sesion, mocker):
    """AI-1 sigue en pie: la validación es contra lo que devolvieron SUS
    consultas, no contra el prompt."""
    mocker.patch(
        "app.services.ai.llm.OpenRouterClient.answer_with_tools",
        autospec=True,
        return_value=("Gastaste S/. 999999.00, un récord.", CONSULTADO),
    )
    mensajes = await _preguntar(sesion, "cuanto gaste?")
    assert not any("999999" in t for t in _textos(mensajes))


@pytest.mark.asyncio
async def test_como_funciona_no_va_al_analista(sesion, analista):
    """Es una explicación del producto, no una cifra de la cuenta: consultarla
    contra la base no aportaría nada."""
    await _preguntar(sesion, "como funciona esto?")
    assert not analista.called
