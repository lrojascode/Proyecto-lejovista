"""Reconciliación mensual: qué histórico falta (execution/03 §3).

El guard anterior preguntaba *"¿ya corrí un backfill?"*. Un backfill cortado a
la mitad quedaba marcado como hecho para siempre. Ahora se compara el gasto que
Meta reporta por mes contra los snapshots que tenemos: la diferencia ES cuánto
falta, medido en plata.
"""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.api_call_log import APICallLog
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.history_reconciliation import (
    DEFAULT_SECONDS_PER_REQUEST,
    get_history_status,
    month_range,
)


async def _org_and_account(db: AsyncSession, slug: str):
    org = Organization(slug=slug, name="Rec Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META", external_account_id="act_rec",
        name="Rec Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


def _prev_month(offset: int) -> str:
    d = datetime.date.today().replace(day=1)
    for _ in range(offset):
        d = (d - datetime.timedelta(days=1)).replace(day=1)
    return d.strftime("%Y-%m")


async def _meta_month(db, org, acc, ym, spend):
    db.add(AdAccountSpendMonthly(
        ad_account_id=acc.id, organization_id=org.id, year_month=ym,
        spend=spend, closed=True,
    ))


async def _local_spend(db, org, acc, ym, spend):
    first, _ = month_range(ym)
    db.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="C", status="ACTIVE", date=first, spend=spend,
    ))


def test_month_range_incluye_el_ultimo_dia():
    assert month_range("2026-02") == (datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
    assert month_range("2024-02") == (datetime.date(2024, 2, 1), datetime.date(2024, 2, 29))  # bisiesto
    assert month_range("2026-12") == (datetime.date(2026, 12, 1), datetime.date(2026, 12, 31))


@pytest.mark.asyncio
async def test_detecta_el_mes_que_falta_y_solo_ese(db_session: AsyncSession):
    org, acc = await _org_and_account(db_session, "rec-hueco")
    completo, hueco = _prev_month(1), _prev_month(2)
    await _meta_month(db_session, org, acc, completo, 1000.0)
    await _meta_month(db_session, org, acc, hueco, 800.0)
    await _local_spend(db_session, org, acc, completo, 1000.0)
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.months_total == 2
    assert status.months_missing == 1
    assert status.gaps[0].year_month == hueco
    assert status.gaps[0].missing_pct == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_cuenta_completa_no_reporta_nada(db_session: AsyncSession):
    """Sobre una cuenta completa esto tiene que costar cero llamadas a Meta —
    es lo que permite correrlo todos los días desde Beat."""
    org, acc = await _org_and_account(db_session, "rec-completa")
    for i in (1, 2, 3):
        ym = _prev_month(i)
        await _meta_month(db_session, org, acc, ym, 500.0)
        await _local_spend(db_session, org, acc, ym, 500.0)
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.is_complete
    assert status.months_covered == 3
    assert status.estimated_requests == 0


@pytest.mark.asyncio
async def test_una_diferencia_chica_no_cuenta_como_hueco(db_session: AsyncSession):
    """Meta reajusta cifras durante días después del cierre. Perseguir el 100%
    haría que el backfill no terminara nunca."""
    org, acc = await _org_and_account(db_session, "rec-tolerancia")
    ym = _prev_month(1)
    await _meta_month(db_session, org, acc, ym, 1000.0)
    await _local_spend(db_session, org, acc, ym, 980.0)  # 2% de diferencia
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.is_complete


@pytest.mark.asyncio
async def test_un_mes_sin_campanas_no_es_un_hueco(db_session: AsyncSession):
    """Sin gasto no hay filas de snapshot, y eso NO significa que falte el
    dato. Es la diferencia entre 'ese mes no hubo campañas' y 'me falta'."""
    org, acc = await _org_and_account(db_session, "rec-mes-vacio")
    await _meta_month(db_session, org, acc, _prev_month(1), 0.0)
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.is_complete
    assert status.months_missing == 0


@pytest.mark.asyncio
async def test_el_mes_en_curso_no_se_reconcilia(db_session: AsyncSession):
    """Está a medio transcurrir: siempre se vería incompleto y el backfill lo
    pediría todos los días."""
    org, acc = await _org_and_account(db_session, "rec-mes-actual")
    actual = datetime.date.today().strftime("%Y-%m")
    await _meta_month(db_session, org, acc, actual, 5000.0)
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.months_total == 0
    assert status.is_complete


@pytest.mark.asyncio
async def test_sin_historico_mensual_no_afirma_estar_completo(db_session: AsyncSession):
    """No hay contra qué comparar. Decir 'completo' sería mentir."""
    org, acc = await _org_and_account(db_session, "rec-sin-mensual")
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.months_total == 0
    assert status.first_month is None


@pytest.mark.asyncio
async def test_aislamiento_por_organizacion(db_session: AsyncSession):
    """Las tablas de archivo no tienen FK por diseño; el filtro por
    organización nunca puede faltar."""
    org_a, acc_a = await _org_and_account(db_session, "rec-org-a")
    org_b, acc_b = await _org_and_account(db_session, "rec-org-b")
    ym = _prev_month(1)
    await _meta_month(db_session, org_a, acc_a, ym, 1000.0)
    await _local_spend(db_session, org_b, acc_b, ym, 1000.0)   # gasto de OTRA org
    await db_session.commit()

    status = await get_history_status(db_session, org_a.id, acc_a.id)
    # El gasto de la org B no puede tapar el hueco de la org A.
    assert status.months_missing == 1


@pytest.mark.asyncio
async def test_los_meses_faltantes_van_del_mas_reciente_al_mas_viejo(db_session: AsyncSession):
    """Si el backfill se corta, lo bajado tiene que ser lo que más se consulta."""
    org, acc = await _org_and_account(db_session, "rec-orden")
    for i in (1, 2, 3):
        await _meta_month(db_session, org, acc, _prev_month(i), 900.0)
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert [g.year_month for g in status.gaps] == [_prev_month(1), _prev_month(2), _prev_month(3)]


@pytest.mark.asyncio
async def test_el_ritmo_sale_de_las_llamadas_reales_de_la_cuenta(db_session: AsyncSession):
    """No es una constante inventada: se deriva del hueco entre llamadas
    consecutivas registradas en APICallLog."""
    org, acc = await _org_and_account(db_session, "rec-ritmo")
    await _meta_month(db_session, org, acc, _prev_month(1), 1000.0)
    base = datetime.datetime.utcnow()
    for i in range(12):
        db_session.add(APICallLog(
            ad_account_id=acc.id, method="GET", endpoint="/insights",
            status_code=200, timestamp=base - datetime.timedelta(seconds=i * 3),
        ))
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.seconds_per_request == pytest.approx(3.0, abs=0.5)
    assert status.seconds_per_request != DEFAULT_SECONDS_PER_REQUEST


@pytest.mark.asyncio
async def test_sin_llamadas_registradas_usa_el_default(db_session: AsyncSession):
    org, acc = await _org_and_account(db_session, "rec-sin-ritmo")
    await _meta_month(db_session, org, acc, _prev_month(1), 1000.0)
    await db_session.commit()

    status = await get_history_status(db_session, org.id, acc.id)
    assert status.seconds_per_request == DEFAULT_SECONDS_PER_REQUEST
