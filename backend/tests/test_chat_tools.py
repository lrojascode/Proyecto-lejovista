"""Las herramientas de consulta del analista.

El chat funcionaba al revés: Python decidía de antemano qué datos meter en una
bolsa fija y el LLM redactaba sobre esa bolsa. Si la pregunta necesitaba algo
que no estaba, no había análisis posible.

Acá el modelo elige qué consultar. Lo que estos tests protegen es que esa
libertad NO alcance al aislamiento entre clientes.
"""
import datetime
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.ad_snapshot import AdSnapshot
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.campaign_chat.tools import (
    TOOL_NAMES, TOOL_SCHEMAS, ChatTools, ejecutar,
)


async def _cuenta(db: AsyncSession, slug: str):
    org = Organization(slug=slug, name=f"Org {slug}", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(organization_id=org.id, platform="META",
                    external_account_id=f"act_{slug}", name=f"Acc {slug}", status="ACTIVE")
    db.add(acc)
    await db.flush()
    return org, acc


async def _campana(db, org, acc, *, cid, nombre, gasto, resultados, dias_atras=1,
                   efectivo="ACTIVE", leads=None):
    db.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id=cid,
        campaign_name=nombre, status="ACTIVE", effective_status=efectivo,
        date=datetime.date.today() - datetime.timedelta(days=dias_atras),
        spend=gasto, impressions=1000, clicks=20,
        conversions=leads if leads is not None else resultados,
        conversations=resultados, results=resultados,
    ))


# --- El aislamiento no es negociable por el modelo ---

def test_ninguna_herramienta_deja_elegir_la_organizacion():
    """Si el LLM pudiera pasar `organization_id`, el aislamiento por tenant
    dependería de que un modelo eligiera bien. El scope se inyecta en el
    constructor y no está en el contrato."""
    contrato = json.dumps(TOOL_SCHEMAS)
    assert "organization_id" not in contrato
    assert "ad_account_id" not in contrato


@pytest.mark.asyncio
async def test_las_consultas_no_cruzan_organizaciones(db_session: AsyncSession):
    org_a, acc_a = await _cuenta(db_session, "tools-a")
    org_b, acc_b = await _cuenta(db_session, "tools-b")
    await _campana(db_session, org_a, acc_a, cid="ca", nombre="De A", gasto=100.0, resultados=10)
    await _campana(db_session, org_b, acc_b, cid="cb", nombre="De B", gasto=999.0, resultados=99)
    await db_session.commit()

    tools = ChatTools(db_session, org_a.id, acc_a.id)
    res = await tools.buscar_campanas(periodo_dias=30)
    assert [c["nombre"] for c in res["campanas"]] == ["De A"]

    resumen = await tools.resumen_cuenta(periodo_dias=30)
    assert resumen["gasto"] == 100.0


# --- Lo que las preguntas reales necesitan ---

@pytest.mark.asyncio
async def test_donde_estoy_perdiendo_plata(db_session: AsyncSession):
    """Las campañas que gastan sin traer nada quedan FUERA de un ranking por
    costo, así que antes el modelo no las veía nunca."""
    org, acc = await _cuenta(db_session, "tools-perdida")
    await _campana(db_session, org, acc, cid="c1", nombre="Rinde", gasto=100.0, resultados=40)
    await _campana(db_session, org, acc, cid="c2", nombre="Quema", gasto=800.0, resultados=0)
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).buscar_campanas(
        periodo_dias=30, solo_sin_resultados=True,
    )
    assert [c["nombre"] for c in res["campanas"]] == ["Quema"]
    assert res["campanas"][0]["gasto"] == 800.0


@pytest.mark.asyncio
async def test_que_esta_corriendo_de_verdad(db_session: AsyncSession):
    """`estado` dice cómo quedó configurada; `estado_efectivo` si entrega."""
    org, acc = await _cuenta(db_session, "tools-entrega")
    await _campana(db_session, org, acc, cid="c1", nombre="Al aire", gasto=50.0,
                   resultados=5, efectivo="ACTIVE")
    await _campana(db_session, org, acc, cid="c2", nombre="Fantasma", gasto=50.0,
                   resultados=5, efectivo="ADSET_PAUSED")
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).buscar_campanas(
        periodo_dias=30, solo_entregando=True,
    )
    assert [c["nombre"] for c in res["campanas"]] == ["Al aire"]


@pytest.mark.asyncio
async def test_el_costo_por_resultado_lo_calcula_la_consulta(db_session: AsyncSession):
    """El modelo tiene PROHIBIDO dividir. Si la herramienta no le devolviera el
    costo ya calculado, o no lo diría o lo inventaría."""
    org, acc = await _cuenta(db_session, "tools-costo")
    await _campana(db_session, org, acc, cid="c1", nombre="X", gasto=250.0, resultados=20)
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).buscar_campanas(periodo_dias=30)
    assert res["campanas"][0]["costo_por_resultado"] == 12.5


@pytest.mark.asyncio
async def test_sin_resultados_el_costo_es_nulo_y_no_cero(db_session: AsyncSession):
    """Un 0 se leería como "gratis". De ahí salía el "S/. 0.00 por contacto"."""
    org, acc = await _cuenta(db_session, "tools-costo-nulo")
    await _campana(db_session, org, acc, cid="c1", nombre="Sin nada", gasto=500.0, resultados=0)
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).buscar_campanas(periodo_dias=30)
    assert res["campanas"][0]["costo_por_resultado"] is None


@pytest.mark.asyncio
async def test_serie_temporal_por_mes(db_session: AsyncSession):
    org, acc = await _cuenta(db_session, "tools-serie")
    for d in (5, 40, 70):
        await _campana(db_session, org, acc, cid=f"c{d}", nombre=f"C{d}",
                       gasto=100.0, resultados=10, dias_atras=d)
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).serie_temporal(
        granularidad="mes", periodo_dias=120,
    )
    assert res["granularidad"] == "mes"
    assert res["puntos"] >= 2
    assert all(len(p["periodo"]) == 7 for p in res["serie"])   # YYYY-MM


@pytest.mark.asyncio
async def test_historico_mensual_llega_mas_atras_que_lo_diario(db_session: AsyncSession):
    org, acc = await _cuenta(db_session, "tools-historico")
    for ym, g in [("2021-03", 1000.0), ("2021-04", 1500.0)]:
        db_session.add(AdAccountSpendMonthly(
            ad_account_id=acc.id, organization_id=org.id, year_month=ym,
            spend=g, closed=True,
        ))
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).gasto_mensual_historico()
    assert res["primer_mes"] == "2021-03"
    assert res["gasto_total_historico"] == 2500.0


@pytest.mark.asyncio
async def test_los_anuncios_traen_su_copy_y_marcan_acortadores(db_session: AsyncSession):
    org, acc = await _cuenta(db_session, "tools-anuncios")
    db_session.add(AdSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1", adset_id="as1",
        ad_id="ad1", name="Anuncio Uno", creative_title="Curso SAP",
        creative_body="Inscribite en bit.ly/curso", creative_cta_type="SIGN_UP",
        date=datetime.date.today() - datetime.timedelta(days=1),
        spend=100.0, impressions=1000, clicks=30, conversions=5, conversations=5, results=5,
    ))
    await db_session.commit()

    res = await ChatTools(db_session, org.id, acc.id).buscar_anuncios(periodo_dias=30)
    anuncio = res["anuncios"][0]
    assert anuncio["titulo"] == "Curso SAP"
    assert "bit.ly" in anuncio["copy"]
    assert anuncio["tiene_acortador"] is True


# --- Errores legibles, no excepciones ---

@pytest.mark.asyncio
async def test_una_herramienta_inexistente_devuelve_error_legible(db_session: AsyncSession):
    org, acc = await _cuenta(db_session, "tools-error")
    await db_session.commit()

    res = await ejecutar(ChatTools(db_session, org.id, acc.id), "inventada", {})
    assert "error" in res
    # El modelo lee esto y reintenta con otra consulta.
    assert "buscar_campanas" in res["error"]


@pytest.mark.asyncio
async def test_un_argumento_invalido_no_tumba_el_turno(db_session: AsyncSession):
    org, acc = await _cuenta(db_session, "tools-argumento")
    await db_session.commit()

    res = await ejecutar(ChatTools(db_session, org.id, acc.id),
                         "buscar_campanas", {"parametro_que_no_existe": 1})
    assert "error" in res


def test_el_catalogo_y_los_metodos_coinciden():
    """Un esquema sin método detrás es una herramienta que el modelo pide y
    siempre falla."""
    for nombre in TOOL_NAMES:
        assert hasattr(ChatTools, nombre), f"'{nombre}' está en el contrato pero no existe"
