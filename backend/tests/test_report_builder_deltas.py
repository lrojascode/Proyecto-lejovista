"""5.2 (F-3.1): ReportBuilder.build() calcula la ventana previa del MISMO
tamaño y agrega deltas_pct a los KPIs y *_delta_pct por campaña, para la
comparativa temporal del reporte con selector 7/30/90 días."""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.user import User
from app.models.organization_user import OrganizationUser
from sqlalchemy import select
from app.services.report_builder import ReportBuilder


async def _make_org_and_account(db: AsyncSession, slug="rbd-org"):
    org = Organization(slug=slug, name="RB Deltas Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_rbd", name="RB Deltas Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_build_computes_kpi_and_campaign_deltas_vs_previous_window(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()

    # Ventana actual (7d): día -1. Spend 100, 10 conversiones.
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=100.0, conversions=10,
    ))
    # Ventana previa (7d antes de esa): día -10. Spend 50, 10 conversiones —
    # mismo volumen de conversiones pero el doble de gasto ahora.
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today - datetime.timedelta(days=10),
        spend=50.0, conversions=10,
    ))
    await db_session.commit()

    report = await ReportBuilder(db_session).build(acc, window_days=7)

    assert report["kpis"]["deltas_pct"]["spend"] == 100.0  # 50 -> 100

    camp = next(c for c in report["campaigns"] if c["campaign_id"] == "camp-1")
    assert camp["spend_delta_pct"] == 100.0
    assert camp["cost_per_conv_delta_pct"] == 100.0  # 5.0 -> 10.0


@pytest.mark.asyncio
async def test_build_delta_is_none_when_no_previous_data(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="rbd-org-nohist")
    today = datetime.date.today()

    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-new",
        campaign_name="Campaña nueva", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=100.0, conversions=10,
    ))
    await db_session.commit()

    report = await ReportBuilder(db_session).build(acc, window_days=7)

    assert report["kpis"]["deltas_pct"]["spend"] is None  # sin historia previa
    camp = next(c for c in report["campaigns"] if c["campaign_id"] == "camp-new")
    assert camp["spend_delta_pct"] is None
    assert camp["cost_per_conv_delta_pct"] is None


@pytest.mark.asyncio
async def test_brief_data_rejects_invalid_window_days(async_client: AsyncClient, db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="rbd-org-badwindow")
    await db_session.commit()
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": "rbd-window@example.com", "password": "securepassword123",
        "full_name": "Test User", "organization_slug": f"{org.slug}-personal",
        "organization_name": "Personal Org",
    })
    token = signup_res.json()["access_token"]
    user = (await db_session.execute(select(User).where(User.email == "rbd-window@example.com"))).scalar_one()
    db_session.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    await db_session.commit()

    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug, "X-Ad-Account-Id": acc.id}
    res = await async_client.get("/api/v1/reports/brief-data?window_days=45", headers=headers)
    assert res.status_code == 400
