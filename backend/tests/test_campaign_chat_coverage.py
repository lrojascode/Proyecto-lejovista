"""Cobertura de datos y honestidad del chat (execution/04 §3, §5, §9).

El origen de todo esto: el chat contestó "tengo acceso a los últimos 7 días" a
una pregunta sobre la base de datos y "en los últimos 36 días" en el turno
siguiente. Los dos números eran correctos dentro de su camino y ninguno
describía la cobertura real.
"""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.campaign_chat.questions import classify_question
from app.services.data_coverage import (
    Coverage, days_of_history, get_account_coverage, narrate_coverage,
)


# --- Clasificación: el choque de regex que había que resolver ---

def test_coverage_le_gana_a_schedule():
    """SCHEDULE ya matcheaba "hasta cuando" y "desde cuando" y estaba antes en
    la lista, así que "¿desde cuándo tenés datos?" devolvía la fecha de fin de
    una CAMPAÑA. El discriminador es el sujeto: datos, no campaña.

    Si alguien reordena _TOPIC_PATTERNS, este test es el que lo frena.
    """
    for pregunta in [
        "hasta que dia tenes acceso en la base de datos?",
        "desde cuando tenes datos?",
        "cuanto historial tenes?",
        "cuantos anos tiene activa la cuenta?",
        "cuando fue la ultima sincronizacion?",
        "estan todos los datos?",
    ]:
        assert classify_question(pregunta) == "COVERAGE", pregunta


def test_las_preguntas_de_campana_siguen_en_schedule():
    """La otra dirección: mover COVERAGE arriba no puede robarle SCHEDULE."""
    for pregunta in [
        "cuando fue creada la campana?",
        "hasta cuando corre la campana?",
        "cuanto lleva corriendo?",
        "cual es la fecha de inicio?",
    ]:
        assert classify_question(pregunta) == "SCHEDULE", pregunta


def test_no_se_roba_las_demas_preguntas():
    assert classify_question("cuanto gaste?") == "SPEND"
    assert classify_question("cual es mi mejor campana?") == "BEST_CAMPAIGN"
    assert classify_question("cuantos leads tuve?") == "LEADS"


# --- Honestidad: lo que el producto NO puede saber ---

def test_ventas_y_roas_no_se_confunden_con_inversion():
    """"¿cuánto vendí?" caía en SPEND y contestaba con la INVERSIÓN, que es
    otra cosa. El píxel mide contactos, no ventas."""
    for pregunta in ["cuanto vendi este mes?", "cual es mi roas?",
                     "cuantos alumnos se matricularon?", "cual es mi retorno de inversion?"]:
        assert classify_question(pregunta) == "NOT_MEASURED", pregunta


def test_otra_cuenta_o_marca_se_rechaza_explicitamente():
    for pregunta in ["y mis otras cuentas?", "cuanto factura la competencia?",
                     "como voy comparado con el promedio del mercado?"]:
        assert classify_question(pregunta) == "OUT_OF_ACCOUNT", pregunta


# --- Cobertura sobre datos reales ---

async def _org_acc(db: AsyncSession, slug: str):
    org = Organization(slug=slug, name="Cov Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META", external_account_id="act_cov",
        name="Cov Acc", status="ACTIVE",
        last_synced_at=datetime.datetime(2026, 8, 20, 6, 0),
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_cuenta_dias_distintos_no_el_rango(db_session: AsyncSession):
    """`max - min` asumía continuidad: un rango con huecos reportaba días que
    no existían. Se cuentan fechas distintas."""
    org, acc = await _org_acc(db_session, "cov-huecos")
    base = datetime.date(2026, 8, 1)
    for offset in (0, 1, 10):          # 3 días dentro de un rango de 11
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
            campaign_name="C", status="ACTIVE", date=base + datetime.timedelta(days=offset),
            spend=10.0,
        ))
    await db_session.commit()

    cov = await get_account_coverage(db_session, org.id, acc)
    assert cov.dias_con_datos == 3
    assert cov.dias_sin_datos == 8
    assert cov.primer_dia == base
    assert cov.ultimo_dia == base + datetime.timedelta(days=10)


@pytest.mark.asyncio
async def test_days_of_history_filtra_por_organizacion(db_session: AsyncSession):
    """La función anterior (kpi_period) era la única query del módulo sin scope
    de organización. Las tablas _archive además no tienen FK por diseño."""
    org_a, acc_a = await _org_acc(db_session, "cov-org-a")
    org_b, acc_b = await _org_acc(db_session, "cov-org-b")
    db_session.add(CampaignSnapshot(
        ad_account_id=acc_b.id, organization_id=org_b.id, campaign_id="c",
        campaign_name="C", status="ACTIVE", date=datetime.date(2026, 8, 1), spend=5.0,
    ))
    await db_session.commit()

    assert await days_of_history(db_session, org_a.id, acc_a.id) == 0
    assert await days_of_history(db_session, org_b.id, acc_b.id) == 1


@pytest.mark.asyncio
async def test_la_antiguedad_sale_del_historico_mensual(db_session: AsyncSession):
    """"¿cuántos años lleva activa?" se responde con el mensual, que llega más
    atrás que el detalle diario."""
    org, acc = await _org_acc(db_session, "cov-antiguedad")
    db_session.add(AdAccountSpendMonthly(
        ad_account_id=acc.id, organization_id=org.id, year_month="2021-03",
        spend=1000.0, closed=True,
    ))
    await db_session.commit()

    cov = await get_account_coverage(db_session, org.id, acc)
    assert cov.primer_mes == "2021-03"
    assert "año" in cov.antiguedad_texto


# --- Narración canónica ---

def test_la_narracion_dice_fechas_completas():
    """El documento pide "18 de agosto de 2026", no "18/08" ni "la semana
    pasada". Se arma en código: una fecha que redacte el modelo trae dígitos que
    el grounding no reconoce y descarta la respuesta entera."""
    texto = narrate_coverage({"cobertura": {
        "dias_con_datos": 890,
        "primer_dia_con_datos": "2024-01-15",
        "ultimo_dia_con_datos": "2026-08-19",
        "antiguedad_de_la_cuenta": "2 años y 7 meses",
        "ultima_sincronizacion": "2026-08-20",
        "historial_completo": True,
    }})
    assert "15 de enero de 2024" in texto
    assert "19 de agosto de 2026" in texto
    assert "890 días" in texto
    assert "2 años y 7 meses" in texto


def test_la_narracion_admite_que_falta_historial():
    texto = narrate_coverage({"cobertura": {
        "dias_con_datos": 36,
        "ultimo_dia_con_datos": "2026-08-20",
        "historial_completo": False,
        "meses_incompletos": ["2026-05", "2026-04"],
    }})
    assert "2026-05" in texto and "2026-04" in texto
    assert "faltan" in texto.lower()


def test_sin_datos_lo_dice_en_vez_de_inventar():
    texto = narrate_coverage({})
    assert "no tengo datos sincronizados" in texto.lower()


def test_el_resumen_de_cobertura_es_chico():
    """Va en TODOS los turnos: tiene que caber en el presupuesto de grounding
    (MAX_GROUNDING_VALUES = 220) sin desplazar las cifras de la pregunta."""
    cov = Coverage(
        primer_dia=datetime.date(2024, 1, 15), ultimo_dia=datetime.date(2026, 8, 19),
        dias_con_datos=890,
    )
    assert set(cov.resumen()) == {
        "dias_con_datos_en_total", "primer_dia_con_datos", "ultimo_dia_con_datos",
    }
