"""Tests del histórico de inversión mensual (sin desglose por campaña):
backfill de una sola vez por cuenta vía `time_increment=monthly`, luego solo
se actualiza el mes en curso — nunca se repite la descarga completa. Ver
MetaAdsSyncService._sync_spend_history y GET /reports/spend-history."""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.services.meta_ads.sync import MetaAdsSyncService


async def _make_org_and_account(db: AsyncSession, slug="spend-org"):
    org = Organization(slug=slug, name="Spend Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Spend Acc", status="ACTIVE",
    )
    acc.set_access_token("valid-token")
    db.add(acc)
    await db.flush()
    return org, acc


def _shift_month(d: datetime.date, delta: int) -> datetime.date:
    year, month = d.year, d.month + delta
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return datetime.date(year, month, 1)


def _ym(d: datetime.date) -> str:
    return d.strftime("%Y-%m")


class _SpendFakeLimiter:
    """Simula insights de la cuenta: backfill mensual (call_api_paginated con
    time_increment=monthly) y el total del mes en curso (call_api con
    date_preset=this_month)."""

    def __init__(self, monthly_rows=None, this_month_spend=100.0):
        self.monthly_rows = monthly_rows or []
        self.this_month_spend = this_month_spend
        self.paginated_calls = 0
        self.call_calls = 0

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        assert params.get("time_increment") == "monthly"
        assert params.get("date_preset") == "maximum"
        self.paginated_calls += 1
        return {"data": self.monthly_rows}

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        assert params.get("date_preset") == "this_month"
        self.call_calls += 1
        return {"data": [{"spend": str(self.this_month_spend)}]}


@pytest.mark.asyncio
async def test_backfill_populates_months_and_closes_old_ones(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()
    rows = [
        {"date_start": _shift_month(today, -2).isoformat(), "spend": "500.0"},
        {"date_start": _shift_month(today, -1).isoformat(), "spend": "600.0"},
        {"date_start": today.replace(day=1).isoformat(), "spend": "150.0"},
    ]
    limiter = _SpendFakeLimiter(monthly_rows=rows)

    await MetaAdsSyncService(db_session)._sync_spend_history(acc, "valid-token", limiter)
    await db_session.flush()

    fetched = (await db_session.execute(
        select(AdAccountSpendMonthly)
        .where(AdAccountSpendMonthly.ad_account_id == acc.id)
        .order_by(AdAccountSpendMonthly.year_month)
    )).scalars().all()

    assert len(fetched) == 3
    assert fetched[0].closed is True
    assert fetched[1].closed is True
    assert fetched[2].year_month == _ym(today)
    assert fetched[2].closed is False
    assert fetched[2].spend == 150.0
    assert limiter.paginated_calls == 1
    assert limiter.call_calls == 0  # el backfill nunca llama a "this_month"


@pytest.mark.asyncio
async def test_second_sync_does_not_repeat_backfill(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="spend-org-2")
    today = datetime.date.today()
    rows = [{"date_start": today.replace(day=1).isoformat(), "spend": "150.0"}]
    service = MetaAdsSyncService(db_session)

    limiter1 = _SpendFakeLimiter(monthly_rows=rows)
    await service._sync_spend_history(acc, "valid-token", limiter1)
    await db_session.flush()
    assert limiter1.paginated_calls == 1

    limiter2 = _SpendFakeLimiter(this_month_spend=999.0)
    await service._sync_spend_history(acc, "valid-token", limiter2)
    await db_session.flush()

    assert limiter2.paginated_calls == 0  # nunca repite el backfill completo
    assert limiter2.call_calls == 1

    row = (await db_session.execute(select(AdAccountSpendMonthly).where(
        AdAccountSpendMonthly.ad_account_id == acc.id, AdAccountSpendMonthly.year_month == _ym(today),
    ))).scalar_one()
    assert row.spend == 999.0
    assert row.closed is False


@pytest.mark.asyncio
async def test_incremental_lazily_closes_old_open_months(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="spend-org-3")
    today = datetime.date.today()
    prev = _shift_month(today, -1)
    db_session.add(AdAccountSpendMonthly(
        ad_account_id=acc.id, organization_id=org.id, year_month=_ym(prev), spend=42.0, closed=False,
    ))
    await db_session.commit()

    limiter = _SpendFakeLimiter(this_month_spend=10.0)
    await MetaAdsSyncService(db_session)._sync_spend_history(acc, "valid-token", limiter)
    await db_session.flush()

    prev_row = (await db_session.execute(select(AdAccountSpendMonthly).where(
        AdAccountSpendMonthly.ad_account_id == acc.id, AdAccountSpendMonthly.year_month == _ym(prev),
    ))).scalar_one()
    assert prev_row.closed is True
    assert prev_row.spend == 42.0  # cierre perezoso: no se toca el monto ya guardado
    assert limiter.paginated_calls == 0  # ya había historia -> no repite el backfill


@pytest.mark.asyncio
async def test_mock_sync_generates_18_months_once(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="spend-org-mock")
    await db_session.commit()

    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    rows = (await db_session.execute(
        select(AdAccountSpendMonthly).where(AdAccountSpendMonthly.ad_account_id == acc.id)
    )).scalars().all()
    assert len(rows) == 18

    # Un segundo sync (mock) no debe duplicar ni regenerar el histórico.
    await MetaAdsSyncService(db_session).run_sync(acc, "INCREMENTAL")
    rows_after = (await db_session.execute(
        select(AdAccountSpendMonthly).where(AdAccountSpendMonthly.ad_account_id == acc.id)
    )).scalars().all()
    assert len(rows_after) == 18


async def _signup_with_ad_account(async_client: AsyncClient, slug: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Spend Tester", "organization_slug": slug, "organization_name": "Spend Org",
    })
    body = signup_res.json()
    token = body["access_token"]
    org_id = body["user"]["organization_id"]
    headers = {"Authorization": f"Bearer {token}"}

    acc_res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={
            "platform": "META", "external_account_id": "act_1",
            "name": "Spend Acc", "access_token": "mock-token",
        },
        headers=headers,
    )
    return headers, org_id, acc_res.json()["id"]


@pytest.fixture(autouse=True)
def _mock_ad_account_sync(mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")


@pytest.mark.asyncio
async def test_spend_history_endpoint_requires_auth(async_client: AsyncClient):
    res = await async_client.get("/api/v1/reports/spend-history")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_spend_history_endpoint_returns_scoped_months(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id, ad_account_id = await _signup_with_ad_account(async_client, "spend-endpoint-org")

    today = datetime.date.today()
    db_session.add(AdAccountSpendMonthly(
        ad_account_id=ad_account_id, organization_id=org_id,
        year_month=_ym(today), spend=321.5, closed=False,
    ))
    await db_session.commit()

    res = await async_client.get("/api/v1/reports/spend-history", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["ad_account_id"] == ad_account_id
    assert body["months"] == [{"year_month": _ym(today), "spend": 321.5, "closed": False}]
