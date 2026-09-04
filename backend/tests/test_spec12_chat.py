"""Spec 12 — Chat proactivo, brief y contexto multimodal.

Un test por criterio de aceptación de
`docs/specs/12-chat-proactivo-brief-y-contexto-multimodal.md`.

Tres decisiones de la spec sobre cómo se prueba, respetadas aquí:

* **La frontera es el recorrido completo por API**: apertura, resumen, consulta,
  brief, medios y solicitud. No transiciones sueltas de la máquina de estados,
  que ya tienen sus propias pruebas.
* **Las cuatro situaciones degradadas se prueban por separado y contra la
  ausencia de datos.** Cada una tiene que producir algo distinguible de las
  otras tres: el riesgo es precisamente que se confundan, así que probarlas de a
  una no alcanza — hay un test que las compara entre sí.
* **La ausencia de vocabulario interno se comprueba sobre las respuestas de un
  recorrido completo**, no revisando plantillas. Revisar plantillas encuentra lo
  que alguien escribió a mano y se pierde lo que llega desde una excepción.
"""
import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ai_insight import AIInsight
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.chat_notice import ChatNoticeLog
from app.services.baseline import POR_DEBAJO_DEL_BASELINE, VALIDADA
from app.services.campaign_chat import (
    avisos as avisos_mod,
    brief as brief_mod,
    contexto as contexto_mod,
    degradacion as degradacion_mod,
    medios_contexto,
    resumen as resumen_mod,
    vocabulario,
)
from tests.test_campaign_chat_flow import _signup_with_ad_account_and_page, _turn_id
from tests.test_campaign_chat_gates import _pasar_las_compuertas_del_brief

HOY = datetime.date(2026, 9, 1)


@pytest.fixture(autouse=True)
def _sin_efectos_externos(mocker):
    """Crear una cuenta dispara un sync por Celery, y la suite apunta Redis a un
    puerto muerto: sin esto cada alta cuelga veinte segundos reintentando.

    Es el mismo montaje que usan los demás archivos de chat. Se repite aquí
    porque una fixture `autouse` no viaja con la función que se importa.
    """
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    mocker.patch(
        "app.services.campaign_creation.CampaignValidator.validate",
        autospec=True, return_value=None,
    )
    fabrica = mocker.patch("app.services.campaign_creation.chain")
    instancia = fabrica.return_value
    instancia.on_error.return_value = instancia
    return instancia


def _snapshot(acc_id, org_id, cid, *, dia, gasto, conversaciones, nombre=None):
    return CampaignSnapshot(
        ad_account_id=acc_id, organization_id=org_id,
        campaign_id=cid, campaign_name=nombre or f"Camp {cid}", status="ACTIVE",
        objective="OUTCOME_LEADS", optimization_goal="CONVERSATIONS",
        spend=gasto, conversations=conversaciones,
        date=datetime.date.today() - datetime.timedelta(days=dia),
    )


async def _cuenta_con_historial(db: AsyncSession, org_id: str, acc_id: str):
    """Dos campañas: una barata y sostenida, otra cara. Da algo que resumir."""
    for d in range(1, 6):
        db.add(_snapshot(acc_id, org_id, "buena", dia=d, gasto=20.0,
                         conversaciones=10, nombre="VERANO MENSAJES"))
        db.add(_snapshot(acc_id, org_id, "cara", dia=d, gasto=60.0,
                         conversaciones=2, nombre="INVIERNO MENSAJES"))
    await db.commit()


# --------------------------------------------------------------------------
# Criterios 1 y 2 — el chat abre con contenido.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_al_abrir_el_chat_hay_resumen_sin_preguntar_nada(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "apertura-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    res = await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )
    assert res.status_code == 201, res.text
    session = res.json()

    primero = session["messages"][0]
    # `is not None` y no `in`: el campo está DECLARADO en el esquema, así que
    # la clave existe siempre y comprobar su presencia no probaría nada.
    assert primero.get("resumen") is not None, "el chat abre con contenido, no con un cursor"
    assert primero["resumen"]["hay_actividad"] is True
    # Las cifras del resumen, en el texto.
    assert "400" in primero["text"] or "S/" in primero["text"]
    # Y qué preguntar después: sin esto el cliente lee el resumen y vuelve a
    # quedarse ante el cursor.
    assert primero.get("quick_replies")


@pytest.mark.asyncio
async def test_c2_el_resumen_existe_igual_con_el_modelo_caido(
    async_client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    """Es la diferencia entre un producto que degrada y uno que se queda en
    blanco. El resumen es determinista de punta a punta."""
    from app.config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "sin-llm-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    session = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()

    primero = session["messages"][0]
    assert primero["resumen"]["hay_actividad"] is True
    assert primero["origen"] == "determinista"


def test_c2_el_resumen_se_redacta_sin_llamar_a_nadie():
    """Puro: sin base, sin red. Es lo que garantiza que exista siempre."""
    r = resumen_mod.construir([
        {"campaign_id": "a", "nombre": "Verano", "gasto": 300.0,
         "conversaciones": 30, "leads": 0, "dias": 10},
    ])
    texto = resumen_mod.redactar(r)
    assert texto and "7 días" in texto


def test_c2_una_cuenta_sin_actividad_tambien_tiene_resumen():
    """"No pasó nada" es una respuesta, y es distinta de una pantalla vacía."""
    texto = resumen_mod.redactar(resumen_mod.construir([]))
    assert "no hubo movimiento" in texto


# --------------------------------------------------------------------------
# Criterios 3, 4 y 5 — el contexto viaja con la respuesta.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c3_la_respuesta_declara_la_cuenta_y_el_periodo(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "contexto-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    session = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()

    ctx = session["messages"][0]["contexto"]
    assert ctx["cuenta"]["id"] == acc_id
    assert ctx["cuenta"]["nombre"]
    assert ctx["periodo"]["dias"] == resumen_mod.VENTANA_DIAS
    assert ctx["periodo"]["desde"] and ctx["periodo"]["hasta"]


@pytest.mark.asyncio
async def test_c4_una_cuenta_sin_sincronizar_declara_la_frescura(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Una cifra sin fecha invita a decidir sobre una falsa sensación de
    actualidad: el cliente no puede distinguir "no pasó nada" de "no nos
    enteramos"."""
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "frescura-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    session = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()

    frescura = session["messages"][0]["contexto"]["frescura"]
    assert frescura["hay_datos"] is False
    assert frescura["desactualizado"] is True
    assert frescura["motivo"]
    # Y también en el texto: la interfaz puede no renderizar el bloque.
    assert "sincroniza" in session["messages"][0]["text"].lower()


def test_c4_la_frescura_se_declara_tambien_cuando_esta_al_dia():
    """Siempre, no solo cuando hay problema: es lo que permite distinguir los
    dos casos que se ven igual en pantalla."""
    class _Cuenta:
        last_synced_at = datetime.datetime.utcnow() - datetime.timedelta(hours=2)

    f = contexto_mod.frescura_de(_Cuenta())
    assert f.hay_datos is True
    assert f.desactualizado is False
    assert f.motivo is None
    assert f.as_dict()["ultima_sincronizacion"]


def test_c4_pasadas_las_horas_del_umbral_se_declara_desactualizado():
    class _Cuenta:
        last_synced_at = datetime.datetime.utcnow() - datetime.timedelta(
            hours=contexto_mod.HORAS_PARA_DESACTUALIZADO + 1
        )

    f = contexto_mod.frescura_de(_Cuenta())
    assert f.desactualizado is True
    assert "horas" in f.motivo


@pytest.mark.asyncio
async def test_c5_sin_evidencia_suficiente_se_declara_en_vez_de_afirmar(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Con muestra corta la cifra se muestra —es real— pero no se concluye."""
    headers, _org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "suficiencia-org",
    )

    session = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()

    suficiencia = session["messages"][0]["contexto"]["suficiencia"]
    assert suficiencia["nivel"] == contexto_mod.SUFICIENCIA_INSUFICIENTE
    assert suficiencia["permite_afirmar"] is False
    assert suficiencia["motivo"]


def test_c5_no_aplica_no_es_lo_mismo_que_insuficiente():
    """Clasificar como insuficiente lo que simplemente no necesita muestra haría
    que el cliente leyera una advertencia donde no hay nada que advertir."""
    neutra = contexto_mod.Suficiencia()
    assert neutra.nivel == contexto_mod.SUFICIENCIA_NO_APLICA
    assert neutra.permite_afirmar is True
    assert contexto_mod.insuficiente("poca muestra").permite_afirmar is False


# --------------------------------------------------------------------------
# Criterio 6 — mejoraron o empeoraron, con los estados de `06`.
# --------------------------------------------------------------------------

def test_c6_el_resumen_usa_los_estados_de_la_spec_06():
    """"Va bien" no significa nada y sobre tres conversaciones sería mentira.
    El chat presenta lo que el clasificador produce y no clasifica por su
    cuenta."""
    from app.services.baseline import Baseline

    base = Baseline(mediana=2.0, campanas_comparables=6,
                    conversaciones_totales=120, gasto_total=240.0)
    r = resumen_mod.construir([
        {"campaign_id": "b", "nombre": "Buena", "gasto": 100.0,
         "conversaciones": 60, "leads": 0, "dias": 10},
        {"campaign_id": "c", "nombre": "Cara", "gasto": 400.0,
         "conversaciones": 20, "leads": 0, "dias": 10},
    ], baseline=base)

    estados = set(r.por_estado)
    assert estados <= {"datos_insuficientes", "prometedora", "validada",
                       "por_debajo_del_baseline"}
    assert VALIDADA in estados and POR_DEBAJO_DEL_BASELINE in estados


def test_c6_una_muestra_pequena_nunca_recibe_etiqueta_definitiva():
    r = resumen_mod.construir([
        {"campaign_id": "x", "nombre": "Nueva", "gasto": 4.0,
         "conversaciones": 2, "leads": 0, "dias": 1},
    ])
    assert set(r.por_estado) == {"datos_insuficientes"}


# --------------------------------------------------------------------------
# Criterio 7 — la cuenta de la sesión se congela.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_cambiar_la_cuenta_activa_no_mueve_una_sesion_abierta(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Cambiar de cuenta a mitad de conversación produciría un hilo cuyos
    primeros mensajes hablan de otra cuenta."""
    from conftest import cuenta_de_prueba

    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "congelada-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    otra = await cuenta_de_prueba(db_session, org_id, external_account_id="act_otra")
    await db_session.commit()

    # Con dos cuentas hay que elegir una explícitamente: el producto se niega a
    # adivinar cuál (`MULTIPLE_AD_ACCOUNTS_NO_SELECTION`). Se abre la sesión
    # sobre la PRIMERA.
    con_primera = {**headers, "X-Ad-Account-Id": acc_id}
    creada = await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=con_primera,
    )
    assert creada.status_code == 201, creada.text
    session_id = creada.json()["id"]

    # El cliente cambia de cuenta activa a mitad de conversación.
    con_otra = {**headers, "X-Ad-Account-Id": otra.id}
    session = (await async_client.get(
        f"/api/v1/campaigns/chat/sessions/{session_id}", headers=con_otra,
    )).json()

    assert session["ad_account_id"] == acc_id


# --------------------------------------------------------------------------
# Criterios 8 y 9 — el brief mínimo.
# --------------------------------------------------------------------------

def test_c8_el_brief_sabe_que_le_falta():
    estado = brief_mod.estado_del_brief({})
    assert not estado.completo
    assert estado.siguiente is not None
    # Se pide de a uno: siete preguntas en un mensaje de chat no se contestan.
    assert estado.siguiente.pregunta


@pytest.mark.parametrize("ausente", brief_mod.OBLIGATORIOS)
def test_c9_falta_cualquiera_de_los_obligatorios_y_el_brief_no_esta_completo(ausente):
    """Se omite cada elemento obligatorio de uno en uno, como pide la spec."""
    completo = {
        "goal_text": "vender termas", "product_name": "Termas", "budget": 300.0,
        "days": 7, "whatsapp_number": "51999999999", "image_path": "org/x.jpg",
        "capacidad_de_atencion": {"texto": "20 al día", "consultas_por_dia": 20},
    }
    desde_slots = brief_mod._DESDE_SLOTS[ausente]
    incompleto = {k: v for k, v in completo.items() if k not in desde_slots}

    assert brief_mod.estado_del_brief(completo).completo is True
    assert brief_mod.estado_del_brief(incompleto).completo is False
    assert ausente in brief_mod.estado_del_brief(incompleto).faltantes


def test_c8_la_capacidad_de_atencion_es_obligatoria_y_dice_por_que():
    """Es el elemento menos obvio: una campaña que trae más consultas de las que
    el negocio puede atender EMPEORA su negocio aunque mejore la métrica."""
    elemento = brief_mod.POR_CLAVE[brief_mod.CAPACIDAD_DE_ATENCION]
    assert elemento.obligatorio is True
    assert elemento.porque and "no recibe respuesta" in elemento.porque


def test_c8_los_opcionales_no_bloquean():
    """Exigirlos convertiría el brief en un interrogatorio y el cliente
    abandonaría antes de llegar a la propuesta."""
    slots = {
        "goal_text": "x", "product_name": "y", "budget": 100.0, "days": 7,
        "whatsapp_number": "51999999999", "image_path": "org/x.jpg",
        "capacidad_de_atencion": {"texto": "las que sean"},
    }
    estado = brief_mod.estado_del_brief(slots)
    assert estado.completo is True
    assert estado.opcionales_faltantes


def test_c8_la_capacidad_sin_numero_es_una_respuesta_valida():
    """"Las que sean" es una respuesta legítima de un negocio chico, y
    rechazarla obligaría al cliente a inventar una cifra."""
    interpretado = brief_mod.interpretar_capacidad("las que lleguen")
    assert interpretado["texto"]
    assert interpretado["consultas_por_dia"] is None


@pytest.mark.asyncio
async def test_c9_la_solicitud_no_llega_a_propuesta_sin_el_brief_completo(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Criterio 9, por la API. Proponer con elementos obligatorios ausentes
    obliga al sistema a rellenarlos, que es inventar la decisión de negocio del
    cliente."""
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "brief-gate-org",
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id}, headers=headers,
    )).json()["id"]

    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "quiero vender termas"},
        headers=headers,
    )
    session = (await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/x.jpg"},
        headers=headers,
    )).json()

    # Con la foto puesta pero sin brief, NO hay propuesta todavía.
    assert session["state"] in ("REVIEW_MEDIA", "ASK_BRIEF")
    assert not any(m["type"] == "review_card" for m in session["messages"])

    session = await _pasar_las_compuertas_del_brief(
        async_client, headers, session_id, session,
    )
    assert session["state"] == "REVIEW_PLAN"
    assert any(m["type"] == "review_card" for m in session["messages"])


# --------------------------------------------------------------------------
# Criterios 10, 11 y 12 — el contexto del medio.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c10_al_adjuntar_un_medio_se_muestra_que_se_entendio(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Una interpretación equivocada que nadie ve se convierte en una campaña
    equivocada."""
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "medio-org",
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id}, headers=headers,
    )).json()["id"]
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "quiero vender termas"},
        headers=headers,
    )

    session = (await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/x.jpg"},
        headers=headers,
    )).json()

    assert session["state"] == "REVIEW_MEDIA"
    revision = next(
        m for m in session["messages"] if m.get("medio_contexto") is not None
    )
    assert revision["medio_contexto"]["revisado"] is False
    assert revision["text"]


@pytest.mark.asyncio
async def test_c11_la_correccion_del_cliente_manda_y_llega_a_la_propuesta(
    async_client: AsyncClient, db_session: AsyncSession,
):
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "correccion-org",
    )
    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id}, headers=headers,
    )).json()["id"]
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "quiero vender termas"},
        headers=headers,
    )
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/x.jpg"},
        headers=headers,
    )

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/media-context",
        json={"client_turn_id": _turn_id(), "producto": "Calentadores Solares"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    session = res.json()

    # La corrección manda: el producto de la propuesta es el corregido.
    assert session["slots"]["product_name"] == "Calentadores Solares"


def test_c11_lo_medido_del_archivo_no_se_puede_corregir():
    """El archivo mide lo que mide. Aceptar una corrección ahí dejaría al
    sistema mandándole a Meta unas dimensiones que la imagen no tiene."""
    base = medios_contexto.ContextoDelMedio(kind="IMAGE", ancho=1200, alto=628)
    with pytest.raises(medios_contexto.CorreccionInvalida):
        medios_contexto.corregir(base, {"ancho": 9999})

    corregido = medios_contexto.corregir(base, {"producto": "Termas"})
    assert corregido.ancho == 1200
    assert corregido.efectivo("producto") == "Termas"


def test_c11_la_correccion_se_guarda_junto_a_lo_extraido_no_encima():
    """Conservar las dos permite responder en qué se equivoca la
    interpretación, que es lo único que permite mejorarla."""
    base = medios_contexto.ContextoDelMedio(producto="Termas eléctricas")
    corregido = medios_contexto.corregir(base, {"producto": "Termas a gas"})

    assert corregido.producto == "Termas eléctricas", "lo extraído se conserva"
    assert corregido.correcciones["producto"] == "Termas a gas"
    assert corregido.efectivo("producto") == "Termas a gas", "manda la corrección"


def test_c12_la_revision_del_medio_no_promete_resultados():
    """Nadie sabe si una creatividad va a funcionar antes de gastar. El
    rendimiento no se lee en los píxeles (spec 10, historia 17)."""
    from app.services.ai.guardas import promesa_de_exito

    contexto = medios_contexto.con_interpretacion(
        medios_contexto.ContextoDelMedio(kind="IMAGE", ancho=1200, alto=628),
        producto="Termas", descripcion="una terma solar en un techo",
    )
    for linea in medios_contexto.texto_de_revision(contexto):
        assert promesa_de_exito(linea) is None, linea


# --------------------------------------------------------------------------
# Criterios 13, 14 y 15 — las cuatro situaciones, distinguibles entre sí.
# --------------------------------------------------------------------------

def test_c13_c14_c15_las_cuatro_situaciones_son_distinguibles():
    """El riesgo del criterio es que se confundan, así que probarlas de a una no
    alcanza: hay que compararlas entre sí."""
    cuatro = [
        degradacion_mod.ia_no_disponible(),
        degradacion_mod.meta_no_disponible(),
        degradacion_mod.no_autorizado(),
        degradacion_mod.sin_datos(),
    ]

    situaciones = {d.situacion for d in cuatro}
    assert situaciones == set(degradacion_mod.SITUACIONES), "las cuatro, sin repetir"

    mensajes = [d.mensaje for d in cuatro]
    assert len(set(mensajes)) == 4, "cuatro mensajes distintos"

    textos = [d.como_texto() for d in cuatro]
    assert len(set(textos)) == 4


def test_c13_la_caida_de_la_ia_conserva_lo_determinista_y_lo_dice():
    d = degradacion_mod.ia_no_disponible()
    assert d.situacion == degradacion_mod.IA_NO_DISPONIBLE
    assert d.es_falta_de_datos is False
    # Nombra lo que SIGUE funcionando: es lo que decide si el cliente puede
    # seguir trabajando o tiene que volver luego.
    assert "datos están completos" in d.sigue_disponible


def test_c14_la_caida_de_meta_habla_de_frescura_no_de_ausencia():
    d = degradacion_mod.meta_no_disponible(
        {"horas_desde_la_sincronizacion": 50, "motivo": "hace 50 horas"},
    )
    assert d.situacion == degradacion_mod.META_NO_DISPONIBLE
    assert "50" in d.mensaje
    assert "no es que no haya datos" in d.sigue_disponible.lower()


def test_c15_el_fallo_de_autorizacion_no_se_disfraza_de_estado_conversacional():
    """Un 403 que llega como "no encontré datos de esa cuenta" le enseña al
    cliente que la cuenta está vacía cuando lo que pasa es que no le
    corresponde."""
    d = degradacion_mod.no_autorizado()
    assert d.situacion == degradacion_mod.NO_AUTORIZADO
    assert "permiso" in d.mensaje
    assert "no quiere decir que la cuenta esté vacía" in d.sigue_disponible


def test_la_ausencia_de_datos_no_es_una_degradacion():
    """Nada está roto: la cuenta es nueva. Meterla en el mismo saco haría que un
    cliente nuevo creyera que el producto falla el primer día."""
    d = degradacion_mod.sin_datos()
    assert d.es_falta_de_datos is True
    assert "ningún problema técnico" in d.sigue_disponible


@pytest.mark.asyncio
async def test_c13_con_el_presupuesto_de_ia_agotado_el_chat_sigue_respondiendo(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Y lo dice nombrando la situación, no con un texto que el cliente pueda
    confundir con "no hay datos"."""
    from decimal import Decimal

    from app.models.ai_token_usage import AITokenUsage
    from app.models.organization import Organization

    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "ia-caida-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    org = await db_session.get(Organization, org_id)
    org.ai_monthly_budget_usd = Decimal("1.00")
    db_session.add(AITokenUsage(
        organization_id=org_id, feature="campaign_chat", provider="openai",
        model="gpt-5.6-luna", total_tokens=10,
        estimated_cost_usd=1.0, estimated_cost_usd_exact=Decimal("1.000000"),
    ))
    await db_session.commit()

    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()["id"]

    session = (await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "cuanto gaste este mes?"},
        headers=headers,
    )).json()

    degradado = next(
        (m for m in session["messages"] if m.get("degradacion") is not None), None,
    )
    assert degradado is not None, "la caída de la IA se nombra"
    assert degradado["degradacion"]["situacion"] == degradacion_mod.IA_NO_DISPONIBLE
    # Y siguió respondiendo con lo determinista.
    assert len([m for m in session["messages"] if m.get("role") == "assistant"]) >= 2


# --------------------------------------------------------------------------
# Criterio 16 — nada de vocabulario interno.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "El Draft falló.",
    "La Saga terminó con error.",
    "Se aplicó el grounding a la respuesta.",
    "El write-back no está habilitado.",
    "La AIAction quedó pendiente.",
])
def test_c16_el_vocabulario_interno_se_traduce(texto):
    traducido = vocabulario.traducir(texto)
    assert vocabulario.terminos_internos_en(traducido) == [], traducido


def test_c16_la_traduccion_no_rompe_la_frase():
    """Una traducción que deja el texto peor que el original no la usaría
    nadie, y el guardián acabaría desactivado."""
    assert vocabulario.traducir("El Draft falló.") == "Tu solicitud falló."
    assert vocabulario.traducir("La Saga terminó bien.").startswith(
        "La creación de tu campaña"
    )


@pytest.mark.asyncio
async def test_c16_un_recorrido_completo_no_muestra_vocabulario_interno(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Se comprueba sobre las RESPUESTAS de un recorrido completo, no sobre las
    plantillas: revisar plantillas encuentra lo que alguien escribió a mano y se
    pierde lo que llega desde una excepción."""
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "vocab-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id}, headers=headers,
    )).json()["id"]
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "quiero vender termas"},
        headers=headers,
    )
    session = (await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_id}/x.jpg"},
        headers=headers,
    )).json()
    session = await _pasar_las_compuertas_del_brief(
        async_client, headers, session_id, session,
    )

    encontrados = []
    for texto in vocabulario.recolectar_textos(session["messages"]):
        encontrados += [(t, texto) for t in vocabulario.terminos_internos_en(texto)]

    assert encontrados == [], f"vocabulario interno en la superficie: {encontrados[:3]}"


# --------------------------------------------------------------------------
# Criterio 17 — los avisos no se repiten en cada apertura.
# --------------------------------------------------------------------------

class _Insight:
    def __init__(self, id, level="WARNING", category="PERFORMANCE",
                 title="Campaña cara", description="Gastó sin resultados.",
                 campaign_id=None, is_dismissed=False):
        self.id, self.level, self.category = id, level, category
        self.title, self.description = title, description
        self.campaign_id, self.is_dismissed = campaign_id, is_dismissed
        self.created_at = datetime.datetime(2026, 9, 1)


def test_c17_un_aviso_ya_entregado_no_vuelve_dentro_de_la_ventana():
    ahora = datetime.datetime(2026, 9, 2, 12, 0)
    insight = _Insight("i1", campaign_id="c1")
    huella = avisos_mod.huella_de(insight)

    entregar, descartados = avisos_mod.seleccionar(
        [insight], {huella: ahora - datetime.timedelta(hours=1)}, ahora=ahora,
    )
    assert entregar == []
    assert len(descartados) == 1


def test_c17_pasada_la_ventana_el_aviso_puede_volver():
    """Vuelve, pero no cada vez: el hallazgo sigue vigente y a alguien puede
    seguirle importando."""
    ahora = datetime.datetime(2026, 9, 10, 12, 0)
    insight = _Insight("i1", campaign_id="c1")
    huella = avisos_mod.huella_de(insight)
    horas = avisos_mod.ventana_horas("WARNING")

    entregar, _ = avisos_mod.seleccionar(
        [insight], {huella: ahora - datetime.timedelta(hours=horas + 1)}, ahora=ahora,
    )
    assert len(entregar) == 1


def test_c17_la_ventana_depende_del_nivel():
    """La misma ventana para todos convertiría lo urgente en ruido o lo
    accesorio en insistencia."""
    assert avisos_mod.ventana_horas("CRITICAL") < avisos_mod.ventana_horas("WARNING")
    assert avisos_mod.ventana_horas("WARNING") < avisos_mod.ventana_horas("INFO")


def test_c17_la_huella_no_depende_de_como_este_redactado_hoy():
    """El texto lleva las cifras del día y cambia solo. Si la huella dependiera
    de él, un céntimo de diferencia haría un aviso nuevo y el límite no serviría
    para nada."""
    a = _Insight("i1", campaign_id="c1", description="Gastó S/ 100.00 sin resultados.")
    b = _Insight("i2", campaign_id="c1", description="Gastó S/ 100.01 sin resultados.")
    assert avisos_mod.huella_de(a) == avisos_mod.huella_de(b)


def test_c17_no_se_entregan_mas_de_los_que_se_leen():
    """Una lista de ocho se cierra entera sin mirar cuál era el importante."""
    insights = [_Insight(f"i{n}", campaign_id=f"c{n}") for n in range(8)]
    entregar, _ = avisos_mod.seleccionar(insights, {})
    assert len(entregar) == avisos_mod.MAX_POR_APERTURA


def test_c17_lo_cortado_por_tope_no_cuenta_como_silenciado_por_frecuencia():
    """Sería mentir en la única métrica que dice si la ventana está bien
    calibrada."""
    insights = [_Insight(f"i{n}", campaign_id=f"c{n}") for n in range(8)]
    _entregar, descartados = avisos_mod.seleccionar(insights, {})
    assert descartados == []


def test_lo_urgente_va_antes_que_lo_accesorio():
    insights = [
        _Insight("info", level="INFO", campaign_id="c1"),
        _Insight("critico", level="CRITICAL", campaign_id="c2"),
        _Insight("warning", level="WARNING", campaign_id="c3"),
    ]
    entregar, _ = avisos_mod.seleccionar(insights, {})
    assert [a.nivel for a in entregar] == ["CRITICAL", "WARNING", "INFO"]


def test_los_hallazgos_de_agencia_no_se_le_entregan_al_cliente():
    """Le darían un problema que no puede resolver: tocar placements es trabajo
    de la agencia."""
    entregar, _ = avisos_mod.seleccionar(
        [_Insight("p", category="PLACEMENT", campaign_id="c1")], {},
    )
    assert entregar == []


@pytest.mark.asyncio
async def test_c17_abrir_dos_veces_no_repite_el_mismo_aviso(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """El recorrido completo del criterio: dos aperturas seguidas."""
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "avisos-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    db_session.add(AIInsight(
        ad_account_id=acc_id, organization_id=org_id, campaign_id="cara",
        level="WARNING", category="PERFORMANCE",
        title="Campaña sin resultados",
        description="Gastó sin traer ninguna consulta.",
    ))
    await db_session.commit()

    primera = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()
    assert any(m.get("avisos") for m in primera["messages"]), "la primera vez sí"

    segunda = (await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )).json()
    assert not any(m.get("avisos") for m in segunda["messages"]), (
        "el mismo aviso no vuelve en la apertura siguiente"
    )

    # Y quedó registrado que se entregó: sin el registro no hay límite.
    entregas = (await db_session.execute(
        select(ChatNoticeLog).where(ChatNoticeLog.organization_id == org_id)
    )).scalars().all()
    assert len(entregas) == 1


# --------------------------------------------------------------------------
# Observabilidad del chat.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_la_observabilidad_dice_si_el_chat_esta_sirviendo(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Cuatro cosas que no se pueden ver desde dentro de una conversación."""
    headers, org_id, acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "obs-chat-org",
    )
    await _cuenta_con_historial(db_session, org_id, acc_id)

    await async_client.post(
        "/api/v1/campaigns/chat/sessions",
        json={"ad_account_id": acc_id, "mode": "ASK"}, headers=headers,
    )

    res = await async_client.get(
        "/api/v1/campaigns/chat/observabilidad", headers=headers,
    )
    assert res.status_code == 200, res.text
    cuerpo = res.json()

    assert cuerpo["sesiones_abiertas"] == 1
    assert cuerpo["con_resumen_entregado"] == 1
    assert cuerpo["respuestas_deterministas"] >= 1


@pytest.mark.asyncio
async def test_el_cliente_no_ve_la_observabilidad_del_chat(
    async_client: AsyncClient, db_session: AsyncSession,
):
    from app.models.organization_user import OrganizationUser
    from app.services.experiencia import ACCESO_CLIENTE

    headers, org_id, _acc_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "obs-cliente-org",
    )
    membresia = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org_id)
    )).scalars().first()
    membresia.access_kind = ACCESO_CLIENTE
    await db_session.commit()

    res = await async_client.get(
        "/api/v1/campaigns/chat/observabilidad", headers=headers,
    )
    assert res.status_code == 403
