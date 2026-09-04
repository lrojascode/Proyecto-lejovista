"""Spec 11 — Recuperación, ranking y aprendizaje por cliente.

Un test por criterio de aceptación de
`docs/specs/11-recuperacion-ranking-y-aprendizaje-por-cliente.md`.

Dos decisiones de la spec sobre cómo se prueba, respetadas aquí:

* **La frontera es la API de propuesta y de revisión**, más el registro de
  trazabilidad consultable. Se verifican el conjunto comparable, el baseline y
  la trazabilidad; **no** el algoritmo interno de ranking.
* **Los datasets son el entregable central**: comparables, valores extremos,
  muestras pequeñas, temporadas distintas, cuentas múltiples y ausencia total de
  evidencia. Están al principio del archivo, con nombre, para que un caso nuevo
  se añada al dataset y no como un test suelto.

El aislamiento se prueba con una Organización que tiene DOS cuentas pobladas y
un usuario con acceso a las dos. Con una sola cuenta, un filtro roto pasaría
igual: no habría nada ajeno que pudiera aparecer.
"""
import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.models.proposal_evidence import (
    MOTIVO_OTRO,
    MOTIVO_PRESUPUESTO_INSUFICIENTE,
    REGLA_ACEPTADA,
    REGLA_PROPUESTA,
    REGLA_RECHAZADA,
    REPETICIONES_PARA_PROPONER,
    CandidateRule,
    ProposalChange,
    ProposalEvidence,
)
from app.models.snapshot_archive import CampaignSnapshotArchive
from app.services import evidencia as ev
from app.services.comparabilidad import (
    EVENTO_CONVERSACIONES,
    EVENTO_DESCONOCIDO,
    EVENTO_LEADS,
    EXCLUIDA_EVENTO,
    EXCLUIDA_OBJETIVO,
    EXCLUIDA_SIN_RESULTADOS,
    comparar,
    conjunto_comparable,
    evento_de_conversion,
)
from app.services.recuperacion import (
    MotivoRequerido,
    detectar_reglas_candidatas,
    recuperar_comparables,
    registrar_cambio_humano,
    resolver_regla_candidata,
    validar_motivo,
)
from conftest import cuenta_de_prueba

HOY = datetime.date(2026, 9, 1)


# --------------------------------------------------------------------------
# Datasets. El entregable central de esta spec.
# --------------------------------------------------------------------------

def _campana(cid, *, objetivo="OUTCOME_ENGAGEMENT", goal="CONVERSATIONS",
             gasto=200.0, conversaciones=20, leads=0, dias=7, fecha=None, nombre=None):
    return {
        "campaign_id": cid,
        "nombre": nombre or f"Campaña {cid}",
        "objetivo": objetivo,
        "optimization_goal": goal,
        "gasto": gasto,
        "conversaciones": conversaciones,
        "leads": leads,
        "dias": dias,
        "fecha": fecha or (HOY - datetime.timedelta(days=30)),
    }


# La propuesta contra la que se compara todo el archivo.
REFERENCIA = _campana("PROPUESTA", gasto=200.0, conversaciones=0, fecha=HOY)

# Seis campañas del mismo objetivo y evento, costos consistentes (~10 por
# resultado) y volumen suficiente. Es el conjunto que SÍ debe sostener una
# recomendación.
DATASET_COMPARABLE = [
    _campana("c1", gasto=200.0, conversaciones=20),
    _campana("c2", gasto=220.0, conversaciones=20),
    _campana("c3", gasto=180.0, conversaciones=19),
    _campana("c4", gasto=210.0, conversaciones=21),
    _campana("c5", gasto=190.0, conversaciones=18),
    _campana("c6", gasto=205.0, conversaciones=20),
]

# Mismo tamaño, costos que van de 2 a 90 por resultado. La media diría lo mismo
# que arriba; la dispersión es lo que lo distingue.
DATASET_DISPERSO = [
    _campana("d1", gasto=40.0, conversaciones=20),
    _campana("d2", gasto=900.0, conversaciones=10),
    _campana("d3", gasto=60.0, conversaciones=20),
    _campana("d4", gasto=800.0, conversaciones=10),
    _campana("d5", gasto=50.0, conversaciones=20),
    _campana("d6", gasto=850.0, conversaciones=10),
]

# Dos campañas. Suficiente para una mediana aritmética, insuficiente para
# afirmar nada.
DATASET_MUESTRA_PEQUENA = [
    _campana("p1", gasto=200.0, conversaciones=20),
    _campana("p2", gasto=210.0, conversaciones=20),
]

# Mismo objetivo y evento, pero medio año atrás: temporada opuesta.
DATASET_OTRA_TEMPORADA = [
    _campana(f"t{i}", fecha=HOY - datetime.timedelta(days=250)) for i in range(1, 7)
]

# Combinaciones que la comparabilidad tiene que EXCLUIR, no ponderar a la baja.
DATASET_INCOMPATIBLE = [
    _campana("x_objetivo", objetivo="OUTCOME_TRAFFIC"),
    _campana("x_evento", goal="LEAD_GENERATION", conversaciones=0, leads=20),
    _campana("x_sin_resultados", conversaciones=0, leads=0, gasto=300.0),
]


# --------------------------------------------------------------------------
# Criterio 4 — objetivo y evento de conversión son condiciones duras.
# --------------------------------------------------------------------------

def test_c4_otro_objetivo_nunca_entra_en_el_conjunto():
    resultado = comparar(REFERENCIA, _campana("x", objetivo="OUTCOME_TRAFFIC"))
    assert resultado.comparable is False
    assert resultado.motivo_exclusion == EXCLUIDA_OBJETIVO


def test_c4_otro_evento_de_conversion_nunca_entra():
    """Mismo objetivo, resultado distinto. Una campaña medida en conversaciones
    y otra en formularios producen costos que no se pueden poner en la misma
    escala aunque la etiqueta del objetivo coincida."""
    resultado = comparar(
        REFERENCIA, _campana("x", goal="LEAD_GENERATION", conversaciones=0, leads=20),
    )
    assert resultado.comparable is False
    assert resultado.motivo_exclusion == EXCLUIDA_EVENTO


def test_c4_las_incompatibles_se_excluyen_no_se_ponderan_a_cero():
    """Ponderar hacia cero las deja DENTRO con peso minúsculo, y un conjunto de
    treinta con veintiocho pesos minúsculos parece grande."""
    incluidas, excluidas = conjunto_comparable(REFERENCIA, DATASET_INCOMPATIBLE)
    assert incluidas == []
    assert {e.motivo_exclusion for e in excluidas} == {
        EXCLUIDA_OBJETIVO, EXCLUIDA_EVENTO, EXCLUIDA_SIN_RESULTADOS,
    }


def test_c4_una_campana_comparable_si_entra():
    """Contracaso. Sin él, la comparabilidad se aprueba excluyendo todo."""
    incluidas, _ = conjunto_comparable(REFERENCIA, DATASET_COMPARABLE)
    assert len(incluidas) == len(DATASET_COMPARABLE)


def test_c4_el_evento_se_deriva_cuando_meta_no_lo_dio():
    """Las campañas anteriores a la columna no tienen `optimization_goal`. El
    evento se lee por la otra cara: qué métrica primaria tuvieron."""
    assert evento_de_conversion({"conversaciones": 10}) == EVENTO_CONVERSACIONES
    assert evento_de_conversion({"leads": 10}) == EVENTO_LEADS
    # Sin goal y sin resultados no se sabe, y no saberlo no es comparable.
    assert evento_de_conversion({}) == EVENTO_DESCONOCIDO


def test_c4_dos_desconocidos_no_son_comparables_entre_si():
    """No se es comparable por compartir la misma ignorancia."""
    a = {"campaign_id": "a", "objetivo": "OUTCOME_ENGAGEMENT"}
    b = {"campaign_id": "b", "objetivo": "OUTCOME_ENGAGEMENT"}
    assert comparar(a, b).comparable is False


# --------------------------------------------------------------------------
# Criterios 5 y 6 — cantidad y nivel de confianza.
# --------------------------------------------------------------------------

def test_c5_la_propuesta_declara_cuantas_comparables_y_su_confianza():
    incluidas, excluidas = conjunto_comparable(REFERENCIA, DATASET_COMPARABLE)
    evidencia = ev.construir(incluidas, excluidas)

    assert evidencia.confianza.campanas == 6
    assert evidencia.confianza.nivel == ev.CONFIANZA_ALTA
    assert evidencia.confianza.sostiene_recomendacion is True
    assert evidencia.exploratoria is False


def test_c6_una_muestra_pequena_produce_confianza_baja():
    incluidas, excluidas = conjunto_comparable(REFERENCIA, DATASET_MUESTRA_PEQUENA)
    evidencia = ev.construir(incluidas, excluidas)

    assert evidencia.confianza.nivel == ev.CONFIANZA_BAJA
    assert any("comparable" in m for m in evidencia.confianza.motivos)
    assert evidencia.exploratoria is True


def test_c6_un_conjunto_disperso_produce_confianza_baja():
    """Seis campañas, igual que el conjunto sano. Lo que las separa es que sus
    costos van de 2 a 90 por resultado: la referencia no describe a ninguna."""
    incluidas, excluidas = conjunto_comparable(REFERENCIA, DATASET_DISPERSO)
    evidencia = ev.construir(incluidas, excluidas)

    assert len(incluidas) == 6, "el tamaño no es lo que las distingue"
    assert evidencia.confianza.nivel == ev.CONFIANZA_BAJA
    assert any("dispers" in m for m in evidencia.confianza.motivos)


def test_c6_la_confianza_se_degrada_nunca_se_promueve():
    """Muchas campañas no compensan una dispersión enorme. Sumar puntos por
    virtudes permitiría exactamente esa compensación."""
    grande_y_disperso = DATASET_DISPERSO * 3
    incluidas, excluidas = conjunto_comparable(REFERENCIA, [
        {**c, "campaign_id": f"{c['campaign_id']}_{i}"}
        for i, c in enumerate(grande_y_disperso)
    ])
    evidencia = ev.construir(incluidas, excluidas)

    assert len(incluidas) == 18
    assert evidencia.confianza.nivel == ev.CONFIANZA_BAJA


def test_c6_la_confianza_dice_por_que():
    """"Confianza baja" sin decir por qué no sirve para decidir si conviene
    esperar más datos o cambiar de enfoque."""
    incluidas, excluidas = conjunto_comparable(REFERENCIA, DATASET_MUESTRA_PEQUENA)
    evidencia = ev.construir(incluidas, excluidas)
    assert evidencia.confianza.motivos
    assert evidencia.confianza.as_dict()["motivos"]


def test_c6_otra_temporada_baja_el_parecido_pero_no_excluye():
    """La temporada es un GRADO, no una condición dura: excluirla dejaría a
    toda cuenta estacional sin evidencia la mitad del año."""
    incluidas, _ = conjunto_comparable(REFERENCIA, DATASET_OTRA_TEMPORADA)
    assert len(incluidas) == len(DATASET_OTRA_TEMPORADA)
    assert all(comp.grado < 0.5 for _, comp in incluidas)


# --------------------------------------------------------------------------
# Criterio 7 — sin evidencia, propuesta exploratoria.
# --------------------------------------------------------------------------

def test_c7_sin_comparables_la_propuesta_es_exploratoria():
    evidencia = ev.construir([], [])

    assert evidencia.exploratoria is True
    assert evidencia.confianza.nivel == ev.CONFIANZA_NULA
    assert evidencia.confianza.sostiene_recomendacion is False


def test_c7_la_propuesta_exploratoria_se_funda_en_buenas_practicas_marcadas():
    """Y NO en un histórico que no existe. Presentar una buena práctica general
    como si saliera de los datos del cliente es la mentira concreta que este
    criterio impide."""
    afirmaciones = ev.afirmaciones_exploratorias()

    assert afirmaciones[0].registro == ev.HECHO
    assert "no tiene campañas anteriores comparables" in afirmaciones[0].texto
    # El resto son hipótesis, nunca hechos: son buenas prácticas, no datos suyos.
    assert all(a.registro == ev.HIPOTESIS for a in afirmaciones[1:])
    assert len(afirmaciones) > 1


# --------------------------------------------------------------------------
# Criterios 8 y 9 — hechos, asociaciones e hipótesis.
# --------------------------------------------------------------------------

def test_c8_cada_afirmacion_lleva_su_registro_marcado():
    """Estructura, no palabras en el texto: un matiz de redacción el LLM lo
    pierde, un campo no."""
    for constructor, esperado in (
        (ev.hecho, ev.HECHO), (ev.asociacion, ev.ASOCIACION), (ev.hipotesis, ev.HIPOTESIS),
    ):
        d = constructor("algo").as_dict()
        assert d["registro"] == esperado
        assert d["registro_label"]
        assert d["registro_explicacion"]


def test_c8_un_registro_inventado_se_rechaza():
    with pytest.raises(ValueError):
        ev.Afirmacion("algo", "certeza_absoluta")


def test_c9_la_asociacion_dice_que_no_es_causa():
    """Sin esta explicación, "Asociación" es una etiqueta que el cliente
    interpreta como quiere, y lo más probable es que la lea como causa."""
    explicacion = ev.EXPLICACION_DEL_REGISTRO[ev.ASOCIACION]
    assert "no quiere decir que una cause la otra" in explicacion.lower()


def test_c9_ninguna_afirmacion_del_sistema_usa_lenguaje_causal():
    """El lenguaje causal solo se admite tras una prueba controlada, y este
    alcance no construye ninguna. Se revisa el texto que ESCRIBE el código."""
    causales = ("porque", "causa", "provoca", "hace que", "gracias a", "debido a")
    textos = [a.texto.lower() for a in ev.afirmaciones_exploratorias()]
    textos += [p.lower() for p in ev.BUENAS_PRACTICAS]
    for texto in textos:
        assert not any(c in texto for c in causales), texto


# --------------------------------------------------------------------------
# Criterio 11 — el motivo del cambio humano es obligatorio y estructurado.
# --------------------------------------------------------------------------

def test_c11_sin_motivo_no_se_guarda():
    with pytest.raises(MotivoRequerido):
        validar_motivo(None, "cambié el presupuesto")
    with pytest.raises(MotivoRequerido):
        validar_motivo("", "cambié el presupuesto")


def test_c11_un_motivo_fuera_del_conjunto_se_rechaza():
    with pytest.raises(MotivoRequerido):
        validar_motivo("me_dio_la_gana", None)


def test_c11_otro_exige_escribir_cual():
    """La válvula que evita que el conjunto acotado mienta: sin ella, quien no
    encuentre su motivo elegirá el más parecido y el histórico quedará
    contaminado con correcciones mal clasificadas."""
    with pytest.raises(MotivoRequerido):
        validar_motivo(MOTIVO_OTRO, None)
    motivo, detalle = validar_motivo(MOTIVO_OTRO, "el cliente cambió de producto")
    assert motivo == MOTIVO_OTRO and detalle


def test_c11_un_motivo_del_conjunto_se_acepta():
    motivo, detalle = validar_motivo(MOTIVO_PRESUPUESTO_INSUFICIENTE, None)
    assert motivo == MOTIVO_PRESUPUESTO_INSUFICIENTE
    assert detalle is None


# --------------------------------------------------------------------------
# Aislamiento y archivo, contra base de datos.
# --------------------------------------------------------------------------

async def _org_con_dos_cuentas(db: AsyncSession, async_client: AsyncClient, slug: str):
    """Una Organización con DOS cuentas pobladas y un usuario que alcanza las dos.

    Es el montaje que hace falta para probar el aislamiento: con una sola cuenta
    un filtro roto pasaría igual, porque no habría nada ajeno que pudiera
    aparecer.
    """
    alta = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@ejemplo.com", "password": "securepassword123",
        "full_name": "Agencia", "organization_slug": slug, "organization_name": slug,
    })
    headers = {"Authorization": f"Bearer {alta.json()['access_token']}"}

    org = (await db.execute(
        select(Organization).where(Organization.slug == slug)
    )).scalar_one()

    propia = await cuenta_de_prueba(db, org.id, external_account_id="act_propia")
    hermana = await cuenta_de_prueba(db, org.id, external_account_id="act_hermana")
    await db.commit()
    return headers, org, propia, hermana


async def _usuario_de(db: AsyncSession, org_id: str) -> str:
    """El id de un usuario que exista de verdad en esta Organización.

    `resuelta_por_user_id` es una clave foránea. Un identificador inventado pasa
    contra el motor embebido, que no comprueba las claves foráneas por defecto, y
    rompe contra PostgreSQL — que es donde corre la suite desde la spec 14.
    """
    from app.models.organization_user import OrganizationUser

    return (await db.execute(
        select(OrganizationUser.user_id).where(OrganizationUser.organization_id == org_id)
    )).scalars().first()


def _fila(acc, org_id, cid, *, dia, gasto, conversaciones,
          objetivo="OUTCOME_ENGAGEMENT", goal="CONVERSATIONS", nombre=None, modelo=None):
    modelo = modelo or CampaignSnapshot
    extra = {}
    if modelo is not CampaignSnapshot:
        # El archivo conserva de qué fila caliente vino, y es obligatorio: una
        # fila archivada sin origen no se puede rastrear hasta su snapshot.
        extra["original_id"] = f"orig-{cid}-{dia}"
        extra["created_at"] = datetime.datetime(2026, 2, 1)
    return modelo(
        **extra,
        ad_account_id=acc.id, organization_id=org_id,
        campaign_id=cid, campaign_name=nombre or f"Camp {cid}", status="ACTIVE",
        objective=objetivo, optimization_goal=goal,
        spend=gasto, conversations=conversaciones,
        date=HOY - datetime.timedelta(days=dia),
    )


@pytest.mark.asyncio
async def test_c2_y_c3_el_conjunto_solo_trae_campanas_de_la_cuenta_activa(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Criterios 2 y 3. El usuario alcanza las dos cuentas y aun así el conjunto
    comparable de una nunca contiene campañas de la otra.

    ADR-11 deja de ser solo aislamiento de LECTURA y pasa a serlo también de
    derivación de conocimiento: dos cuentas de la misma Organización describen
    negocios distintos aunque la factura sea la misma.
    """
    _headers, org, propia, hermana = await _org_con_dos_cuentas(
        db_session, async_client, "aisla-org",
    )

    for i in range(6):
        db_session.add(_fila(propia, org.id, f"propia{i}", dia=30 + i,
                             gasto=200.0, conversaciones=20, nombre=f"PROPIA {i}"))
        db_session.add(_fila(hermana, org.id, f"hermana{i}", dia=30 + i,
                             gasto=200.0, conversaciones=20, nombre=f"HERMANA {i}"))
    await db_session.commit()

    evidencia = await recuperar_comparables(
        db_session, propia,
        referencia={"campaign_id": None, "objetivo": "OUTCOME_ENGAGEMENT",
                    "optimization_goal": "CONVERSATIONS", "gasto": 200.0, "fecha": HOY},
        hasta=HOY,
    )

    ids = {c["campaign_id"] for c in evidencia.campanas}
    assert ids, "la cuenta propia tiene histórico comparable"
    assert all(i.startswith("propia") for i in ids), ids
    assert not any(i.startswith("hermana") for i in ids)


@pytest.mark.asyncio
async def test_c16_una_campana_archivada_puede_ser_evidencia(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Criterio 16. Si el archivo no contara, el producto perdería su propuesta
    de valor a medida que envejece: justo las cuentas con más histórico serían
    las que menos evidencia tendrían."""
    _headers, org, acc, _hermana = await _org_con_dos_cuentas(
        db_session, async_client, "archivo-org",
    )

    # Tres calientes y tres SOLO en el archivo.
    for i in range(3):
        db_session.add(_fila(acc, org.id, f"cal{i}", dia=20 + i,
                             gasto=200.0, conversaciones=20))
    for i in range(3):
        db_session.add(_fila(acc, org.id, f"arch{i}", dia=200 + i,
                             gasto=200.0, conversaciones=20,
                             modelo=CampaignSnapshotArchive))
    await db_session.commit()

    evidencia = await recuperar_comparables(
        db_session, acc,
        referencia={"campaign_id": None, "objetivo": "OUTCOME_ENGAGEMENT",
                    "optimization_goal": "CONVERSATIONS", "gasto": 200.0, "fecha": HOY},
        hasta=HOY,
    )

    ids = {c["campaign_id"] for c in evidencia.campanas}
    assert len([i for i in ids if i.startswith("arch")]) == 3, (
        "las campañas archivadas tienen que poder respaldar una propuesta"
    )
    assert len(ids) == 6


@pytest.mark.asyncio
async def test_c1_el_conjunto_es_identificable_con_periodo_y_costo(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Criterio 1. Sin el periodo, dos campañas del mismo nombre en temporadas
    distintas son indistinguibles; sin el costo, la lista no dice por qué
    respalda nada."""
    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "identif-org",
    )
    for i in range(6):
        for d in range(3):
            db_session.add(_fila(acc, org.id, f"c{i}", dia=30 + i * 5 + d,
                                 gasto=70.0, conversaciones=7, nombre=f"CAMPAÑA {i}"))
    await db_session.commit()

    evidencia = await recuperar_comparables(
        db_session, acc,
        referencia={"campaign_id": None, "objetivo": "OUTCOME_ENGAGEMENT",
                    "optimization_goal": "CONVERSATIONS", "gasto": 210.0, "fecha": HOY},
        hasta=HOY,
    )

    assert evidencia.campanas
    for campana in evidencia.campanas:
        assert campana["campaign_id"]
        assert campana["nombre"]
        assert campana["desde"] and campana["hasta"], "el periodo real, no un solo día"
        assert campana["costo_por_resultado"] == 10.0
        assert campana["resultados"] == 21


@pytest.mark.asyncio
async def test_una_cuenta_sin_historico_produce_evidencia_exploratoria(
    async_client: AsyncClient, db_session: AsyncSession,
):
    _headers, _org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "vacia-org",
    )

    evidencia = await recuperar_comparables(
        db_session, acc,
        referencia={"campaign_id": None, "objetivo": "OUTCOME_ENGAGEMENT",
                    "optimization_goal": "CONVERSATIONS", "gasto": 200.0, "fecha": HOY},
        hasta=HOY,
    )

    assert evidencia.exploratoria is True
    assert evidencia.campanas == ()
    assert evidencia.confianza.nivel == ev.CONFIANZA_NULA


@pytest.mark.asyncio
async def test_las_excluidas_dicen_por_que_no_estan(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """"¿Y por qué no está aquella campaña?" es la primera pregunta de cualquier
    operador que conozca la cuenta. Un conjunto que solo muestra lo que entró no
    la puede responder."""
    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "excluidas-org",
    )
    for i in range(3):
        db_session.add(_fila(acc, org.id, f"ok{i}", dia=30 + i, gasto=200.0, conversaciones=20))
    db_session.add(_fila(acc, org.id, "otro_objetivo", dia=31, gasto=200.0,
                         conversaciones=20, objetivo="OUTCOME_TRAFFIC"))
    db_session.add(_fila(acc, org.id, "sin_result", dia=32, gasto=300.0, conversaciones=0))
    await db_session.commit()

    evidencia = await recuperar_comparables(
        db_session, acc,
        referencia={"campaign_id": None, "objetivo": "OUTCOME_ENGAGEMENT",
                    "optimization_goal": "CONVERSATIONS", "gasto": 200.0, "fecha": HOY},
        hasta=HOY,
    )

    motivos = {e["motivo"] for e in evidencia.as_dict()["excluidas_por_motivo"]}
    assert EXCLUIDA_OBJETIVO in motivos
    assert EXCLUIDA_SIN_RESULTADOS in motivos
    for entrada in evidencia.as_dict()["excluidas_por_motivo"]:
        assert entrada["motivo_legible"], "el motivo tiene que ser legible sin abrir el código"


# --------------------------------------------------------------------------
# Criterios 10, 12 y 13 — trazabilidad y Outcome loop, por la API real.
# --------------------------------------------------------------------------

async def _org_lista_para_crear(async_client, db_session, mocker, slug: str):
    """Organización con histórico comparable y la Saga simulada."""
    mock_chain = mocker.patch("app.services.campaign_creation.chain")
    mock_chain.return_value.on_error.return_value = mock_chain.return_value
    mocker.patch("app.services.campaign_creation.CampaignValidator.validate",
                 autospec=True, return_value=None)

    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@ejemplo.com", "password": "securepassword123",
        "full_name": "Spec11", "organization_slug": slug, "organization_name": slug,
    })
    body = res.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    org_id = body["user"]["organization_id"]

    org = await db_session.get(Organization, org_id)
    org.set_limite_mensual("100000.00")
    db_session.add(org)
    acc = await cuenta_de_prueba(db_session, org_id)
    acc.currency = "PEN"
    db_session.add(acc)
    await db_session.commit()

    # El objetivo del histórico es el que el producto PROPONE
    # (`TENANT_CAMPAIGN_RULES["objective"]`), no uno elegido a mano: si no
    # coinciden, el conjunto comparable sale vacío — y con razón, porque una
    # campaña de otro objetivo no puede respaldar a esta.
    from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES

    hoy = datetime.date.today()
    for i, (gasto, conv) in enumerate([(100, 20), (120, 20), (140, 20),
                                       (110, 20), (130, 20), (125, 20)]):
        for d in range(10):
            db_session.add(CampaignSnapshot(
                ad_account_id=acc.id, organization_id=org_id,
                campaign_id=f"hist{i}", campaign_name=f"Hist {i}", status="ACTIVE",
                objective=TENANT_CAMPAIGN_RULES["objective"],
                optimization_goal=TENANT_CAMPAIGN_RULES["optimization_goal"],
                spend=gasto / 10, conversations=conv // 10,
                date=hoy - datetime.timedelta(days=20 + d),
            ))
    await db_session.commit()
    return headers, org_id, acc


@pytest.mark.asyncio
async def test_c10_crear_una_campana_persiste_toda_la_trazabilidad(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Criterio 10: campañas fuente, ventana, reglas, prompt, modelo, confianza
    y baseline congelado, fijados en el momento de proponer."""
    headers, org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "traza-org",
    )

    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    assert creada.status_code == 202, creada.text
    draft_id = creada.json()["draft_id"]

    fila = (await db_session.execute(
        select(ProposalEvidence).where(ProposalEvidence.draft_id == draft_id)
    )).scalar_one()

    assert fila.campanas_fuente, "las campañas que respaldan la propuesta"
    assert fila.ventana_dias
    assert fila.nivel_confianza in ev.NIVELES
    assert fila.baseline_json.get("mediana"), "el baseline CONGELADO"
    assert fila.prompt_version, "la versión de prompt (spec 10)"
    assert fila.modelo and fila.proveedor
    assert fila.reglas_version
    assert fila.version_id, "la versión de propuesta que se aprobó"


@pytest.mark.asyncio
async def test_c1_y_c5_la_api_expone_la_evidencia_de_la_propuesta(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    headers, _org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "api-evid-org",
    )
    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    draft_id = creada.json()["draft_id"]

    res = await async_client.get(
        f"/api/v1/proposals/drafts/{draft_id}/evidencia", headers=headers,
    )
    assert res.status_code == 200, res.text
    cuerpo = res.json()

    assert cuerpo["campanas_comparables"], "el conjunto identificable"
    assert cuerpo["confianza"]["nivel"] in ev.NIVELES
    assert cuerpo["confianza"]["campanas_comparables"] >= 1
    assert cuerpo["baseline_congelado"]["mediana"]
    # Criterio 8, en la respuesta: cada afirmación con su registro.
    assert cuerpo["afirmaciones"]
    for a in cuerpo["afirmaciones"]:
        assert a["registro"] in (ev.HECHO, ev.ASOCIACION, ev.HIPOTESIS)


@pytest.mark.asyncio
async def test_el_cliente_no_ve_la_evidencia_interna(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Es la vista de revisión de la agencia (dependencia con `05`)."""
    from app.models.organization_user import OrganizationUser
    from app.services.experiencia import ACCESO_CLIENTE

    headers, org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "cliente-evid-org",
    )
    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    draft_id = creada.json()["draft_id"]

    membresia = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org_id)
    )).scalars().first()
    membresia.access_kind = ACCESO_CLIENTE
    await db_session.commit()

    res = await async_client.get(
        f"/api/v1/proposals/drafts/{draft_id}/evidencia", headers=headers,
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_c13_el_veredicto_usa_el_baseline_congelado_no_el_vigente(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Criterio 13, con reloj controlado.

    El baseline vigente se recalibra después de crear la campaña. Juzgarla
    contra el de hoy la premiaría o la castigaría por lo que hicieron OTRAS
    campañas más tarde: eso no mide el resultado de una decisión, mide el ruido
    posterior.
    """
    from app.models.campaign_draft import CampaignDraft
    from app.models.campaign_learning_log import CampaignLearningLog
    from app.services.campaign_outcome import evaluate_learning_log

    headers, org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "congelado-org",
    )
    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    draft_id = creada.json()["draft_id"]

    evidencia = (await db_session.execute(
        select(ProposalEvidence).where(ProposalEvidence.draft_id == draft_id)
    )).scalar_one()
    mediana_congelada = evidencia.baseline_json["mediana"]
    assert mediana_congelada

    # La Saga "creó" la campaña.
    draft = await db_session.get(CampaignDraft, draft_id)
    draft.payload = {**(draft.payload or {}), "result": {"campaign_id": "camp-nueva"}}
    draft.status = "CREATED"
    db_session.add(draft)

    log = (await db_session.execute(
        select(CampaignLearningLog).where(CampaignLearningLog.draft_id == draft_id)
    )).scalar_one()

    # Resultados reales de la campaña nueva, dentro de su ventana.
    inicio = log.created_at.date()
    for d in range(5):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org_id,
            campaign_id="camp-nueva", campaign_name="Nueva", status="ACTIVE",
            objective="OUTCOME_LEADS", optimization_goal="CONVERSATIONS",
            spend=12.0, conversations=2,
            date=inicio + datetime.timedelta(days=d),
        ))

    # Y llega histórico mucho más caro: el baseline VIGENTE se dispara.
    hoy = datetime.date.today()
    for d in range(10):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org_id,
            campaign_id="carisima", campaign_name="Carísima", status="ACTIVE",
            objective="OUTCOME_LEADS", optimization_goal="CONVERSATIONS",
            spend=200.0, conversations=1,
            date=hoy - datetime.timedelta(days=d),
        ))
    await db_session.commit()

    outcome = await evaluate_learning_log(db_session, log)

    assert outcome["expectativa_origen"] == "baseline_congelado"
    assert outcome["expected_cost_per_conv"] == mediana_congelada
    assert outcome["baseline_congelado"]["mediana"] == mediana_congelada


# --------------------------------------------------------------------------
# Criterio 12 — de la propuesta original a la versión aprobada.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c12_se_puede_ver_que_cambio_y_por_que(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """El registro tiene que permitir comparar la propuesta original con la
    versión finalmente aprobada. Sin el motivo, "el presupuesto pasó de 200 a
    350" describe el qué y pierde el para qué, que es lo que enseña."""
    from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES

    headers, _org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "cambio-org",
    )
    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    draft_id = creada.json()["draft_id"]

    nueva = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={
            "config": {
                "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
                "budget": 350.0, "budget_type": "LIFETIME", "days": 7,
                "whatsapp_number": "51999999999", "copy_text": "Texto",
                "image_url": "https://example.com/x.jpg",
            },
            "change_reason": "En temporada alta 200 no alcanza para arrancar.",
            "change_reason_code": "presupuesto_insuficiente",
        },
    )
    assert nueva.status_code == 201, nueva.text

    res = await async_client.get(
        f"/api/v1/proposals/drafts/{draft_id}/evidencia", headers=headers,
    )
    cambios = res.json()["cambios_humanos"]

    assert len(cambios) == 1
    cambio = cambios[0]
    assert cambio["motivo"] == "presupuesto_insuficiente"
    assert cambio["motivo_legible"]
    assert "temporada alta" in cambio["motivo_detalle"]
    assert cambio["cambios"]["budget"] == {"antes": 200.0, "despues": 350.0}
    assert cambio["actor"] == "cambio-org@ejemplo.com"
    assert cambio["version_anterior_id"] and cambio["version_nueva_id"]


@pytest.mark.asyncio
async def test_c11_sin_motivo_la_api_rechaza_la_modificacion(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """La garantía tiene que valer en la superficie, no solo en el servicio."""
    headers, _org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "sin-motivo-org",
    )
    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    draft_id = creada.json()["draft_id"]

    config = {
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 350.0, "budget_type": "LIFETIME", "days": 7,
        "whatsapp_number": "51999999999", "copy_text": "Texto",
        "image_url": "https://example.com/x.jpg",
    }

    sin_codigo = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": config, "change_reason": "porque sí"},
    )
    assert sin_codigo.status_code == 422

    inventado = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": config, "change_reason": "porque sí",
              "change_reason_code": "me_dio_la_gana"},
    )
    assert inventado.status_code == 400
    assert inventado.json()["detail"]["code"] == "CHANGE_REASON_REQUIRED"

    # Y no quedó ninguna versión ni ningún cambio a medias.
    cambios = (await db_session.execute(select(ProposalChange))).scalars().all()
    assert cambios == []


@pytest.mark.asyncio
async def test_los_motivos_se_sirven_desde_el_backend(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Dos listas que hay que sincronizar a mano acaban divergiendo, y la que se
    quedaría vieja es la que ve quien elige el motivo."""
    headers, _org_id, _acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "motivos-org",
    )
    res = await async_client.get("/api/v1/proposals/motivos-de-cambio", headers=headers)
    assert res.status_code == 200
    cuerpo = res.json()
    assert len(cuerpo) >= 5
    otro = next(m for m in cuerpo if m["code"] == "otro")
    assert otro["exige_detalle"] is True
    presupuesto = next(m for m in cuerpo if m["code"] == "presupuesto_insuficiente")
    assert presupuesto["exige_detalle"] is False
    assert all(m["label"] for m in cuerpo)


# --------------------------------------------------------------------------
# Criterios 14 y 15 — reglas candidatas.
# --------------------------------------------------------------------------

async def _repetir_motivo(db: AsyncSession, org_id: str, acc_id: str, veces: int,
                          motivo: str = MOTIVO_PRESUPUESTO_INSUFICIENTE):
    for i in range(veces):
        await registrar_cambio_humano(
            db, organization_id=org_id, ad_account_id=acc_id,
            motivo=motivo, motivo_detalle=None,
            cambios={"budget": {"antes": 200.0, "despues": 350.0}},
            draft_id=f"draft{i}", actor_email="operador@ejemplo.com",
        )
    await db.commit()


@pytest.mark.asyncio
async def test_c14_un_motivo_repetido_genera_una_regla_candidata(
    async_client: AsyncClient, db_session: AsyncSession,
):
    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "regla-org",
    )
    await _repetir_motivo(db_session, org.id, acc.id, REPETICIONES_PARA_PROPONER)

    nuevas = await detectar_reglas_candidatas(db_session, org.id, ad_account_id=acc.id)
    await db_session.commit()

    assert len(nuevas) == 1
    regla = nuevas[0]
    assert regla.motivo == MOTIVO_PRESUPUESTO_INSUFICIENTE
    assert regla.status == REGLA_PROPUESTA
    assert regla.ocurrencias == REPETICIONES_PARA_PROPONER
    # Con sus ocurrencias: "esto se repite" sin ellas es una afirmación que
    # nadie puede verificar antes de aceptarla.
    assert len(regla.ocurrencias_json) == REPETICIONES_PARA_PROPONER


@pytest.mark.asyncio
async def test_c14_por_debajo_del_umbral_no_se_propone_nada(
    async_client: AsyncClient, db_session: AsyncSession,
):
    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "umbral-org",
    )
    await _repetir_motivo(db_session, org.id, acc.id, REPETICIONES_PARA_PROPONER - 1)

    assert await detectar_reglas_candidatas(db_session, org.id, ad_account_id=acc.id) == []


@pytest.mark.asyncio
async def test_c14_la_regla_no_entra_en_vigor_hasta_que_alguien_la_acepta(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Convertir correcciones en política automáticamente sobreajusta a la
    última opinión y hace el comportamiento impredecible para quien lo opera."""
    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "vigor-org",
    )
    await _repetir_motivo(db_session, org.id, acc.id, REPETICIONES_PARA_PROPONER)
    nuevas = await detectar_reglas_candidatas(db_session, org.id, ad_account_id=acc.id)
    await db_session.commit()

    assert nuevas[0].status == REGLA_PROPUESTA
    assert nuevas[0].resuelta_en is None

    actor = await _usuario_de(db_session, org.id)
    await resolver_regla_candidata(db_session, nuevas[0], aceptar=True, actor_user_id=actor)
    await db_session.commit()

    assert nuevas[0].status == REGLA_ACEPTADA
    assert nuevas[0].resuelta_en is not None


@pytest.mark.asyncio
async def test_c14_no_se_duplica_una_propuesta_abierta(
    async_client: AsyncClient, db_session: AsyncSession,
):
    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "dup-org",
    )
    await _repetir_motivo(db_session, org.id, acc.id, REPETICIONES_PARA_PROPONER)

    await detectar_reglas_candidatas(db_session, org.id, ad_account_id=acc.id)
    await db_session.commit()
    segunda = await detectar_reglas_candidatas(db_session, org.id, ad_account_id=acc.id)

    assert segunda == []


@pytest.mark.asyncio
async def test_c15_rechazar_deja_constancia_y_abre_un_plazo(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Criterio 15. Reproponerla al día siguiente la convierte en ruido y
    garantiza que se dejen de leer también las buenas."""
    from app.models.proposal_evidence import DIAS_DE_ESPERA_TRAS_RECHAZO

    _headers, org, acc, _h = await _org_con_dos_cuentas(
        db_session, async_client, "rechazo-org",
    )
    await _repetir_motivo(db_session, org.id, acc.id, REPETICIONES_PARA_PROPONER)
    nuevas = await detectar_reglas_candidatas(db_session, org.id, ad_account_id=acc.id)
    await db_session.commit()

    rechazada_en = datetime.datetime(2026, 9, 1, 12, 0)
    await resolver_regla_candidata(
        db_session, nuevas[0], aceptar=False, actor_user_id=await _usuario_de(db_session, org.id),
        nota="No aplica a esta cuenta.", momento=rechazada_en,
    )
    await db_session.commit()

    assert nuevas[0].status == REGLA_RECHAZADA
    assert nuevas[0].nota_de_resolucion == "No aplica a esta cuenta."

    # Al día siguiente: no vuelve.
    al_dia_siguiente = rechazada_en + datetime.timedelta(days=1)
    assert await detectar_reglas_candidatas(
        db_session, org.id, ad_account_id=acc.id, momento=al_dia_siguiente,
    ) == []

    # Pasado el plazo: puede volver a proponerse.
    mas_tarde = rechazada_en + datetime.timedelta(days=DIAS_DE_ESPERA_TRAS_RECHAZO + 1)
    vuelven = await detectar_reglas_candidatas(
        db_session, org.id, ad_account_id=acc.id, momento=mas_tarde,
    )
    assert len(vuelven) == 1


@pytest.mark.asyncio
async def test_c14_y_c15_por_la_api(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    headers, org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "api-regla-org",
    )
    await _repetir_motivo(db_session, org_id, acc.id, REPETICIONES_PARA_PROPONER)
    await detectar_reglas_candidatas(db_session, org_id, ad_account_id=acc.id)
    await db_session.commit()

    listado = await async_client.get(
        "/api/v1/proposals/reglas-candidatas", headers=headers,
    )
    assert listado.status_code == 200, listado.text
    reglas = listado.json()
    assert len(reglas) == 1
    assert reglas[0]["status"] == REGLA_PROPUESTA
    assert reglas[0]["motivo_legible"]

    resuelta = await async_client.post(
        f"/api/v1/proposals/reglas-candidatas/{reglas[0]['id']}/resolver",
        headers=headers, json={"aceptar": False, "nota": "No en esta cuenta."},
    )
    assert resuelta.status_code == 200, resuelta.text
    assert resuelta.json()["status"] == REGLA_RECHAZADA

    # Ya resuelta, no se puede volver a resolver.
    otra_vez = await async_client.post(
        f"/api/v1/proposals/reglas-candidatas/{reglas[0]['id']}/resolver",
        headers=headers, json={"aceptar": True},
    )
    assert otra_vez.status_code == 400

    # Y ya no aparece en el listado de abiertas.
    abiertas = await async_client.get(
        "/api/v1/proposals/reglas-candidatas", headers=headers,
    )
    assert abiertas.json() == []


@pytest.mark.asyncio
async def test_la_regla_candidata_no_cruza_organizaciones(
    async_client: AsyncClient, db_session: AsyncSession,
):
    """Aislamiento también en el aprendizaje: los patrones de un cliente no
    pueden proponerse como regla de otro (ADR-11 extendida)."""
    _h1, org_a, acc_a, _ = await _org_con_dos_cuentas(db_session, async_client, "cruza-a")
    _h2, org_b, _acc_b, _ = await _org_con_dos_cuentas(db_session, async_client, "cruza-b")

    await _repetir_motivo(db_session, org_a.id, acc_a.id, REPETICIONES_PARA_PROPONER)

    assert await detectar_reglas_candidatas(db_session, org_b.id) == []
    assert len(await detectar_reglas_candidatas(db_session, org_a.id)) == 1


# --------------------------------------------------------------------------
# Observabilidad del aprendizaje.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_el_resumen_de_aprendizaje_responde_si_el_sistema_aprende(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """Si la proporción de exploratorias no baja con el tiempo, o la
    comparabilidad está demasiado estricta o la cuenta no acumula histórico
    utilizable. Las dos causas se arreglan distinto."""
    headers, org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "resumen-org",
    )
    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    assert creada.status_code == 202
    await _repetir_motivo(db_session, org_id, acc.id, REPETICIONES_PARA_PROPONER)

    res = await async_client.get("/api/v1/proposals/aprendizaje", headers=headers)
    assert res.status_code == 200, res.text
    cuerpo = res.json()

    assert cuerpo["propuestas"] == 1
    assert cuerpo["proporcion_exploratorias"] is not None
    assert cuerpo["comparables_promedio"] is not None
    assert cuerpo["confianza_por_nivel"]
    motivos = {c["motivo"] for c in cuerpo["cambios_por_motivo"]}
    assert MOTIVO_PRESUPUESTO_INSUFICIENTE in motivos


@pytest.mark.asyncio
async def test_sin_propuestas_la_proporcion_es_nula_y_no_cero(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """"Sin datos" no es "ninguna exploratoria": un cero ahí se leería como que
    el sistema está respaldando todas sus propuestas."""
    headers, _org_id, _acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "vacio-resumen-org",
    )
    cuerpo = (await async_client.get(
        "/api/v1/proposals/aprendizaje", headers=headers,
    )).json()

    assert cuerpo["propuestas"] == 0
    assert cuerpo["proporcion_exploratorias"] is None
    assert cuerpo["comparables_promedio"] is None


@pytest.mark.asyncio
async def test_la_evidencia_llega_hasta_la_respuesta_de_la_sugerencia(
    async_client: AsyncClient, db_session: AsyncSession, mocker,
):
    """El campo tiene que estar declarado en el modelo de respuesta o FastAPI lo
    descarta en silencio: el servicio lo devolvería y la pantalla no lo vería
    nunca."""
    headers, _org_id, acc = await _org_lista_para_crear(
        async_client, db_session, mocker, "sugerencia-org",
    )

    res = await async_client.get(
        f"/api/v1/campaigns/suggestion?ad_account_id={acc.id}&product_name=Curso",
        headers=headers,
    )
    assert res.status_code == 200, res.text
    evidencia = res.json().get("evidencia")

    assert evidencia is not None, "la evidencia no puede perderse en el borde de salida"
    assert "confianza" in evidencia
    assert "campanas_comparables" in evidencia
    assert evidencia["confianza"]["nivel"] in ev.NIVELES
    assert evidencia["afirmaciones"]
