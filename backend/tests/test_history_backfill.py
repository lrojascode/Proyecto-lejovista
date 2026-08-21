"""Regresión §0.6: historia diaria acotada, paginada e idempotente."""
import datetime
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ad_account import AdAccount
from app.models.ad_snapshot import AdSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.kpi_period import days_of_history
from app.services.meta_ads.sync import MetaAdsSyncService, SYNC_TYPE_BACKFILL


async def _account(db: AsyncSession, slug: str) -> AdAccount:
    org = Organization(slug=slug, name="Backfill Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id,
        platform="META",
        external_account_id=f"act_{slug}",
        name="Backfill Account",
        status="ACTIVE",
    )
    acc.set_access_token("valid-token")
    db.add(acc)
    await db.commit()
    return acc


@pytest.mark.asyncio
async def test_mock_backfill_fills_retention_window_and_is_idempotent(
    db_session: AsyncSession,
):
    acc = await _account(db_session, "mock-history")
    service = MetaAdsSyncService(db_session)

    full = await service.run_sync(acc, "FULL")
    backfill = await service.run_sync(acc, SYNC_TYPE_BACKFILL)

    assert full.status == "COMPLETED"
    assert backfill.status == "COMPLETED"
    assert await days_of_history(db_session, acc.id) == settings.SNAPSHOT_RETENTION_DAYS

    async def count(model):
        return (await db_session.execute(
            select(func.count()).select_from(model).where(model.ad_account_id == acc.id)
        )).scalar_one()

    expected_campaign_rows = 3 * settings.SNAPSHOT_RETENTION_DAYS
    expected_child_rows = expected_campaign_rows * 2
    assert await count(CampaignSnapshot) == expected_campaign_rows
    assert await count(AdsetSnapshot) == expected_child_rows
    assert await count(AdSnapshot) == expected_child_rows

    oldest = (await db_session.execute(
        select(func.min(CampaignSnapshot.date)).where(
            CampaignSnapshot.ad_account_id == acc.id
        )
    )).scalar_one()
    assert oldest == datetime.date.today() - datetime.timedelta(
        days=settings.SNAPSHOT_RETENTION_DAYS - 1
    )

    repeated = await service.run_sync(acc, SYNC_TYPE_BACKFILL)
    assert repeated.status == "COMPLETED"
    assert repeated.records_synced == 0
    assert await count(CampaignSnapshot) == expected_campaign_rows


class _RecordingLimiter:
    def __init__(self):
        self.paginated_calls = []

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        return {"amount_spent": "0", "spend_cap": "0", "account_status": 1}

    async def call_api_paginated(
        self, endpoint, params, method="GET", access_token="", max_pages=50
    ):
        self.paginated_calls.append((endpoint, dict(params)))
        return {"data": []}


@pytest.mark.asyncio
async def test_real_backfill_queries_only_bounded_daily_core_levels(
    db_session: AsyncSession,
):
    acc = await _account(db_session, "real-history")
    limiter = _RecordingLimiter()

    records = await MetaAdsSyncService(db_session)._sync_real_data(
        acc, SYNC_TYPE_BACKFILL, limiter
    )

    assert records == 0
    insight_params = [
        params
        for endpoint, params in limiter.paginated_calls
        if endpoint.endswith("/insights")
    ]
    assert {params["level"] for params in insight_params} == {
        "campaign", "adset", "ad"
    }
    assert all(params["time_increment"] == 1 for params in insight_params)
    assert all("date_preset" not in params for params in insight_params)
    assert all("breakdowns" not in params for params in insight_params)

    expected = {
        "since": (
            datetime.date.today()
            - datetime.timedelta(days=settings.SNAPSHOT_RETENTION_DAYS - 1)
        ).isoformat(),
        "until": datetime.date.today().isoformat(),
    }
    assert all(json.loads(params["time_range"]) == expected for params in insight_params)

    endpoints = {endpoint for endpoint, _ in limiter.paginated_calls}
    assert not any("adimages" in endpoint for endpoint in endpoints)
    assert not any("customaudiences" in endpoint for endpoint in endpoints)


# --- Un mes que falla no puede matar el backfill entero ---

@pytest.mark.asyncio
async def test_un_mes_fallido_no_aborta_los_demas(db_session: AsyncSession, mocker):
    """En producción el backfill murió en el mes 3 de 26 con
    `greenlet_spawn has not been called`, y dejó 24 meses sin bajar.

    La causa: `rollback()` expira TODOS los objetos de la sesión —no lo evita
    `expire_on_commit=False`, que solo aplica al commit— y en async, tocar un
    atributo expirado no puede hacer el refresh porque el acceso al atributo es
    sincrónico. Así que el `except` que existía justo para saltear el mes malo
    se caía él mismo en el primer `ad_account.id` de la vuelta siguiente.
    """
    from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
    from app.models.sync_job import SyncJob
    from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter

    acc = await _account(db_session, "mes-fallido")

    hoy = datetime.date.today()
    meses = []
    for i in range(1, 4):
        ym = (hoy.replace(day=1) - datetime.timedelta(days=31 * i)).strftime("%Y-%m")
        meses.append(ym)
        db_session.add(AdAccountSpendMonthly(
            organization_id=acc.organization_id, ad_account_id=acc.id,
            year_month=ym, spend=5000.0, impressions=0, reach=0, results=0,
        ))
    job = SyncJob(ad_account_id=acc.id, organization_id=acc.organization_id,
                  sync_type=SYNC_TYPE_BACKFILL, status="RUNNING")
    db_session.add(job)
    await db_session.commit()

    # El primer mes explota; los demás tienen que bajarse igual.
    llamadas = []

    async def _falla_el_primero(self, ad_account, sync_type, limiter, insights_period=None):
        llamadas.append(insights_period)
        if len(llamadas) == 1:
            raise RuntimeError("Meta devolvió 500")
        return 10

    mocker.patch.object(MetaAdsSyncService, "_sync_real_data", _falla_el_primero)

    # El rollback real expira todos los objetos de la sesión. Con SQLite eso no
    # siempre fuerza IO al releerlos, así que se expira a mano: es la condición
    # exacta de producción, y sin ella el test pasa con el bug puesto.
    rollback_real = db_session.rollback

    async def _rollback_que_expira():
        await rollback_real()
        db_session.expire_all()

    mocker.patch.object(db_session, "rollback", _rollback_que_expira)

    svc = MetaAdsSyncService(db_session)
    registros = await svc._sync_backfill_by_months(acc, MetaAPIRateLimiter(), job)

    assert len(llamadas) == 3, "se cortó en el mes que falló en vez de seguir"
    assert registros == 20, "los dos meses buenos tenían que contarse"
    assert (job.payload_json or {}).get("months_done") == 2
