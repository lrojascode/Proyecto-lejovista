"""Spec 06 — Métrica primaria, baseline y estados de campaña.

Un test por criterio de aceptación de
`docs/specs/06-metrica-primaria-baseline-y-estados-de-campana.md`.

Dos decisiones sobre cómo se prueba, tomadas del contrato de la spec:

* **Los datasets son el entregable central.** Conjuntos con valor extremo, con
  muestra pequeña, con volumen y costo altos, con una sola conversación barata y
  con ausencia total de comparables. Se verifica el baseline y el estado
  resultante, no el algoritmo que los produce.
* **La fidelidad del archivo se prueba de ida y vuelta**, y el test está escrito
  para que **añadir un campo al Snapshot sin añadirlo al archivo lo haga
  fallar**. Es la única forma de que el hueco no vuelva a abrirse en silencio.
"""
import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_snapshot import AdSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.models.snapshot_archive import (
    AdSnapshotArchive, AdsetSnapshotArchive, CampaignSnapshotArchive,
)
from app.services.baseline import (
    DATOS_INSUFICIENTES, ORIGEN_CONVERSACIONES, ORIGEN_LEADS,
    POR_DEBAJO_DEL_BASELINE, PROMETEDORA, VALIDADA,
    UmbralesDeMadurez, calcular_baseline, costo_por_conversacion, evaluar_campana,
    get_madurez_for_org,
)
from app.services.baseline_repository import baseline_de_la_cuenta, campanas_agregadas
from conftest import cuenta_de_prueba


def _campana(gasto, conversaciones, dias, leads=0):
    return {"gasto": gasto, "conversaciones": conversaciones, "dias": dias, "leads": leads}


# Un conjunto sano de referencia: mediana 6.00 por conversación.
CONJUNTO_SANO = [
    _campana(100, 20, 10), _campana(120, 20, 10), _campana(140, 20, 10),
]


# --------------------------------------------------------------------------
# Criterios 1-3 · ninguna etiqueta definitiva sobre muestra pequeña
# --------------------------------------------------------------------------

def test_c1_sin_los_dias_minimos_no_hay_veredicto():
    """El criterio es "nunca validada ni por debajo del baseline", no "siempre
    datos insuficientes".

    Una campaña con 30 conversaciones en 3 días tiene evidencia de sobra en
    volumen y le falta recorrido: `prometedora` la describe mejor que `datos
    insuficientes`, y sigue sin ser un veredicto. Lo que la spec prohíbe es
    emitir un juicio definitivo, no reconocer una tendencia.
    """
    base = calcular_baseline(CONJUNTO_SANO)
    estado = evaluar_campana(gasto=90, conversaciones=30, dias=3, baseline=base)

    assert estado.estado not in (VALIDADA, POR_DEBAJO_DEL_BASELINE)
    assert estado.estado == PROMETEDORA
    assert "día" in (estado.motivo or ""), "tiene que decir qué le falta"


def test_c1b_sin_dias_ni_volumen_es_datos_insuficientes():
    """El otro lado: sin recorrido y sin volumen no hay nada que decir."""
    base = calcular_baseline(CONJUNTO_SANO)
    estado = evaluar_campana(gasto=6, conversaciones=2, dias=1, baseline=base)
    assert estado.estado == DATOS_INSUFICIENTES


def test_c2_sin_las_conversaciones_minimas_es_datos_insuficientes():
    """Aunque haya superado la ventana temporal."""
    base = calcular_baseline(CONJUNTO_SANO)
    estado = evaluar_campana(gasto=200, conversaciones=2, dias=30, baseline=base)
    assert estado.estado == DATOS_INSUFICIENTES
    assert "conversación" in (estado.motivo or "")


def test_c3_una_unica_conversacion_muy_barata_no_es_validada():
    """El caso que la spec nombra: cumple el costo y no significa nada."""
    base = calcular_baseline(CONJUNTO_SANO)
    estado = evaluar_campana(gasto=1.0, conversaciones=1, dias=1, baseline=base)
    assert estado.estado == DATOS_INSUFICIENTES
    assert estado.estado != VALIDADA


def test_c3b_los_cuatro_estados_se_alcanzan():
    """Un vocabulario del que solo se usara un valor no seria un vocabulario."""
    base = calcular_baseline(CONJUNTO_SANO)
    assert evaluar_campana(gasto=0, conversaciones=0, dias=1, baseline=base).estado == DATOS_INSUFICIENTES
    assert evaluar_campana(gasto=20, conversaciones=5, dias=4, baseline=base).estado == PROMETEDORA
    assert evaluar_campana(gasto=100, conversaciones=25, dias=10, baseline=base).estado == VALIDADA
    assert evaluar_campana(gasto=300, conversaciones=12, dias=10, baseline=base).estado == POR_DEBAJO_DEL_BASELINE


# --------------------------------------------------------------------------
# Criterios 4-5 · mediana, y el conjunto que la sostiene
# --------------------------------------------------------------------------

def test_c4_un_valor_extremo_no_desplaza_el_baseline():
    """El promedio lo mueve un solo valor extremo; la referencia contra la que
    se decide gasto no puede depender de eso."""
    con_extremo = CONJUNTO_SANO + [_campana(5000, 10, 10)]   # 500 por conversación
    base = calcular_baseline(con_extremo)

    assert base.mediana == 6.0
    promedio = sum(c["gasto"] / c["conversaciones"] for c in con_extremo) / len(con_extremo)
    assert promedio > 100, "el promedio sí se habría desplazado"


def test_c4b_la_anomalia_se_excluye_del_calculo():
    con_extremo = CONJUNTO_SANO + [_campana(5000, 10, 10)]
    base = calcular_baseline(con_extremo)
    assert base.campanas_comparables == 3, "la anómala no entra en la referencia"


def test_c5_el_baseline_lleva_siempre_el_tamano_del_conjunto():
    """Una mediana sobre tres campañas diminutas y una sobre treinta grandes son
    cifras distintas aunque coincidan."""
    base = calcular_baseline(CONJUNTO_SANO)
    d = base.as_dict()
    assert d["campanas_comparables"] == 3
    assert d["conversaciones_totales"] == 60
    assert d["gasto_total"] == 360.0
    assert d["origen"] == ORIGEN_CONVERSACIONES


def test_c5b_sin_comparables_no_se_inventa_un_baseline():
    base = calcular_baseline([])
    assert base.hay_baseline is False
    assert base.campanas_comparables == 0

    estado = evaluar_campana(gasto=100, conversaciones=25, dias=10, baseline=base)
    assert estado.estado == DATOS_INSUFICIENTES
    assert "comparables" in (estado.motivo or "")


def test_c5c_una_campana_inmadura_no_sirve_de_referencia():
    """Lo que todavía no se puede juzgar tampoco puede juzgar a otras."""
    base = calcular_baseline([_campana(3, 1, 1), _campana(4, 1, 1)])
    assert base.hay_baseline is False


# --------------------------------------------------------------------------
# Criterio 6 · umbrales configurables por Organización
# --------------------------------------------------------------------------

def test_c6_los_umbrales_cambian_por_organizacion_y_no_se_contaminan():
    """Dos organizaciones con valores distintos en la misma ejecución: si
    hubiera estado compartido, esto lo destaparía."""
    class Org:
        def __init__(self, ov): self.ai_thresholds_overrides = ov

    exigente = get_madurez_for_org(Org({"madurez_conversaciones_minimas": 50}))
    laxa = get_madurez_for_org(Org({"madurez_conversaciones_minimas": 2,
                                    "madurez_dias_minimos": 1,
                                    "madurez_gasto_minimo": 1}))
    assert exigente.conversaciones_minimas == 50
    assert laxa.conversaciones_minimas == 2

    base = calcular_baseline(CONJUNTO_SANO)
    caso = dict(gasto=100, conversaciones=25, dias=10, baseline=base)
    # Con el umbral exigente la misma campaña deja de estar validada; con el laxo
    # sí lo está. Que la exigente caiga en `prometedora` y no en `datos
    # insuficientes` es correcto: tiene volumen, solo que no el que esa
    # organización pide.
    assert evaluar_campana(**caso, umbrales=exigente).estado != VALIDADA
    assert evaluar_campana(**caso, umbrales=laxa).estado == VALIDADA

    # Y el default sigue siendo el declarado en la spec.
    por_defecto = get_madurez_for_org(Org({}))
    assert (por_defecto.dias_minimos, por_defecto.conversaciones_minimas) == (7, 10)


# --------------------------------------------------------------------------
# Criterios 7-8 · baseline congelado frente a baseline vigente
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_y_c8_el_congelado_no_se_mueve_y_el_vigente_si(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """Reevaluar el pasado con el baseline de hoy destruye la posibilidad de
    saber si el sistema acertó."""
    mock_chain = mocker.patch("app.services.campaign_creation.chain")
    mock_chain.return_value.on_error.return_value = mock_chain.return_value
    mocker.patch("app.services.campaign_creation.CampaignValidator.validate",
                 autospec=True, return_value=None)

    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "admin@c7-congelado.example.com", "password": "securepassword123",
        "full_name": "Spec06", "organization_slug": "c7-congelado",
        "organization_name": "Spec06 Org",
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

    hoy = datetime.date.today()
    for i, (gasto, conv) in enumerate([(100, 20), (120, 20), (140, 20)]):
        for d in range(10):
            db_session.add(CampaignSnapshot(
                ad_account_id=acc.id, organization_id=org_id,
                campaign_id=f"hist{i}", campaign_name=f"Hist {i}", status="ACTIVE",
                objective="OUTCOME_ENGAGEMENT",
                spend=gasto / 10, conversations=conv // 10,
                date=hoy - datetime.timedelta(days=20 + d),
            ))
    await db_session.commit()

    vigente_antes = await baseline_de_la_cuenta(db_session, acc.id)
    assert vigente_antes.mediana == 6.0

    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    assert creada.status_code == 202, creada.text

    from app.models.campaign_proposal import CampaignProposalVersion
    version = (await db_session.execute(
        select(CampaignProposalVersion).where(
            CampaignProposalVersion.draft_id == creada.json()["draft_id"]
        )
    )).scalar_one()
    congelado = version.gates_json["baseline_congelado"]
    assert congelado["mediana"] == 6.0
    assert congelado["campanas_comparables"] == 3

    # Llega histórico nuevo, mucho más caro: el baseline VIGENTE se recalibra...
    for d in range(10):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org_id,
            campaign_id="nueva-cara", campaign_name="Nueva cara", status="ACTIVE",
            objective="OUTCOME_ENGAGEMENT",
            spend=40.0, conversations=2,
            date=hoy - datetime.timedelta(days=d),
        ))
    await db_session.commit()

    vigente_despues = await baseline_de_la_cuenta(db_session, acc.id)
    assert vigente_despues.mediana != vigente_antes.mediana, "el vigente se recalibra"

    # ...y el CONGELADO de la campaña ya creada no se mueve.
    await db_session.refresh(version)
    assert version.gates_json["baseline_congelado"]["mediana"] == 6.0


# --------------------------------------------------------------------------
# Criterios 9-10 · el archivo frío no pierde nada
# --------------------------------------------------------------------------

@pytest.mark.parametrize("caliente,archivo", [
    (CampaignSnapshot, CampaignSnapshotArchive),
    (AdsetSnapshot, AdsetSnapshotArchive),
    (AdSnapshot, AdSnapshotArchive),
])
def test_c9_el_archivo_tiene_todas_las_columnas_de_negocio(caliente, archivo):
    """Este test es la salvaguarda del contrato: añadir una columna a una tabla
    caliente sin añadirla al archivo lo hace fallar.

    Así se perdieron `objective`, `results`, `effective_status` y las fechas
    estructurales — alguien añadió la columna y nadie se acordó de la lista, y el
    hueco solo se nota meses después, cuando el aprendizaje deja de encontrar la
    campaña.
    """
    solo_del_mecanismo = {"id", "created_at", "original_id", "archived_at"}
    de_negocio = {c.name for c in caliente.__table__.columns} - solo_del_mecanismo
    en_el_archivo = {c.name for c in archivo.__table__.columns} - solo_del_mecanismo

    faltantes = sorted(de_negocio - en_el_archivo)
    assert not faltantes, (
        f"{caliente.__tablename__} tiene columnas que {archivo.__tablename__} no "
        f"conserva: {faltantes}. Añádelas al modelo de archivo y a la migración."
    )


@pytest.mark.asyncio
async def test_c9b_ida_y_vuelta_por_el_archivador_conserva_los_valores(
    db_session: AsyncSession
):
    """No basta con que la columna exista: el archivador tiene que copiarla."""
    from app.services.snapshot_archiver import archive_old_snapshots

    org = Organization(slug="c9b-archivo", name="Archivo")
    db_session.add(org)
    await db_session.commit()
    acc = await cuenta_de_prueba(db_session, org.id)
    await db_session.commit()

    viejo = datetime.date.today() - datetime.timedelta(days=500)
    original = CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id,
        campaign_id="c-vieja", campaign_name="Campaña vieja", status="ACTIVE",
        effective_status="CAMPAIGN_PAUSED",
        objective="OUTCOME_ENGAGEMENT",
        created_time=datetime.datetime(2025, 1, 5, 10, 0),
        start_time=datetime.datetime(2025, 1, 6, 0, 0),
        stop_time=datetime.datetime(2025, 2, 6, 0, 0),
        spend=250.0, impressions=9000, reach=7000, clicks=300,
        conversions=4, conversations=25, results=29,
        cpl=10.0, ctr=3.3, frequency=1.28,
        date=viejo,
    )
    db_session.add(original)
    await db_session.commit()

    await archive_old_snapshots(db_session, retention_days=400)
    await db_session.commit()

    archivada = (await db_session.execute(
        select(CampaignSnapshotArchive).where(CampaignSnapshotArchive.campaign_id == "c-vieja")
    )).scalar_one()

    # Todos los campos que consume el aprendizaje, con el MISMO valor.
    assert archivada.objective == "OUTCOME_ENGAGEMENT"
    assert archivada.results == 29
    assert archivada.effective_status == "CAMPAIGN_PAUSED"
    assert archivada.created_time == datetime.datetime(2025, 1, 5, 10, 0)
    assert archivada.start_time == datetime.datetime(2025, 1, 6, 0, 0)
    assert archivada.stop_time == datetime.datetime(2025, 2, 6, 0, 0)
    assert archivada.conversations == 25
    assert archivada.spend == 250.0

    # Y la fila caliente ya no está: el archivo es ahora la única copia.
    assert (await db_session.execute(
        select(CampaignSnapshot).where(CampaignSnapshot.campaign_id == "c-vieja")
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_c10_una_campana_archivada_sigue_siendo_comparable(db_session: AsyncSession):
    """El punto de todo lo anterior: si el archivo no cuenta, el producto pierde
    su propuesta de valor a medida que envejece."""
    org = Organization(slug="c10-archivo", name="Archivo")
    db_session.add(org)
    await db_session.commit()
    acc = await cuenta_de_prueba(db_session, org.id)
    await db_session.commit()

    viejo = datetime.date.today() - datetime.timedelta(days=300)
    for i, (gasto, conv) in enumerate([(100, 20), (120, 20), (140, 20)]):
        for d in range(10):
            db_session.add(CampaignSnapshotArchive(
                original_id=f"o{i}{d}", ad_account_id=acc.id, organization_id=org.id,
                campaign_id=f"arch{i}", campaign_name=f"Arch {i}", status="ACTIVE",
                objective="OUTCOME_ENGAGEMENT",
                spend=gasto / 10, conversations=conv // 10, results=conv // 10,
                date=viejo + datetime.timedelta(days=d),
                created_at=datetime.datetime.utcnow(),
            ))
    await db_session.commit()

    filas = await campanas_agregadas(db_session, acc.id)
    assert len(filas) == 3, "las campañas archivadas tienen que aparecer"

    base = await baseline_de_la_cuenta(db_session, acc.id)
    assert base.mediana == 6.0
    assert base.campanas_comparables == 3


# --------------------------------------------------------------------------
# Criterios 11-12 · la métrica primaria se nombra por lo que es
# --------------------------------------------------------------------------

def test_c11_la_respuesta_declara_si_la_cifra_viene_de_conversaciones_o_de_leads():
    """Mezclarlas sin decirlo produce comparaciones falsas entre campañas de
    distinta naturaleza."""
    con_mensajeria = costo_por_conversacion(gasto=100, conversaciones=20, leads=5)
    assert con_mensajeria.origen == ORIGEN_CONVERSACIONES
    assert con_mensajeria.valor == 5.0

    sin_mensajeria = costo_por_conversacion(gasto=100, conversaciones=0, leads=25)
    assert sin_mensajeria.origen == ORIGEN_LEADS
    assert sin_mensajeria.valor == 4.0

    sin_nada = costo_por_conversacion(gasto=100, conversaciones=0, leads=0)
    assert sin_nada.hay_metrica is False


@pytest.mark.asyncio
async def test_c12_ninguna_superficie_presenta_la_metrica_como_venta_o_ingreso(
    async_client: AsyncClient, db_session: AsyncSession
):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "admin@c12-venta.example.com", "password": "securepassword123",
        "full_name": "Spec06", "organization_slug": "c12-venta",
        "organization_name": "Spec06 Org",
    })
    body = res.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    acc = await cuenta_de_prueba(db_session, body["user"]["organization_id"])
    await db_session.commit()

    r = await async_client.get("/api/v1/campaigns/performance", headers=headers)
    assert r.status_code == 200, r.text
    texto = r.text.lower()
    for palabra in ("venta", "ingreso", "roas", "revenue"):
        assert palabra not in texto, f"la métrica no puede presentarse como '{palabra}'"


# --------------------------------------------------------------------------
# Criterios 13-14 · frescura y cobertura
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c14_una_sincronizacion_antigua_se_declara_desactualizada(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Una cifra sin saber de cuándo son los datos invita a decidir sobre una
    falsa sensación de actualidad."""
    from app.models.ad_account import AdAccount

    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "admin@c14-frescura.example.com", "password": "securepassword123",
        "full_name": "Spec06", "organization_slug": "c14-frescura",
        "organization_name": "Spec06 Org",
    })
    body = res.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    acc = await cuenta_de_prueba(db_session, body["user"]["organization_id"])
    await db_session.commit()

    # Sin sincronizar nunca.
    r = await async_client.get("/api/v1/campaigns/performance", headers=headers)
    assert r.json()["frescura"]["desactualizado"] is True

    # Sincronizada hace tres días.
    await db_session.execute(
        update(AdAccount).where(AdAccount.id == acc.id).values(
            last_synced_at=datetime.datetime.utcnow() - datetime.timedelta(days=3)
        )
    )
    await db_session.commit()
    r = await async_client.get("/api/v1/campaigns/performance", headers=headers)
    frescura = r.json()["frescura"]
    assert frescura["desactualizado"] is True
    assert frescura["horas_desde_la_sincronizacion"] > 36

    # Sincronizada hace una hora.
    await db_session.execute(
        update(AdAccount).where(AdAccount.id == acc.id).values(
            last_synced_at=datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        )
    )
    await db_session.commit()
    r = await async_client.get("/api/v1/campaigns/performance", headers=headers)
    assert r.json()["frescura"]["desactualizado"] is False
