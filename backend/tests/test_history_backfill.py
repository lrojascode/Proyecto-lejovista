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
