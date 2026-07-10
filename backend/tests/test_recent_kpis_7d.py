"""4.3 (E-4): /campaigns/recent adjunta el agregado real de 7 días por
campaña (spend_7d/conversations_7d/cost_per_conv_7d), no solo el snapshot del
último día — el ranking "menor CPL" del dashboard comparaba días sueltos que
pueden traer 0 gasto o un pico atípico."""
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


async def _make_org_and_account(db: AsyncSession, slug="kpi7d-org"):
    org = Organization(slug=slug, name="KPI 7d Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_kpi7d", name="Cuenta KPI7d", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


async def _auth_headers(async_client: AsyncClient, db: AsyncSession, org, email: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123",
        "full_name": "Test User", "organization_slug": f"{org.slug}-personal",
        "organization_name": "Personal Org",
    })
    token = signup_res.json()["access_token"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    await db.commit()
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug}


@pytest.mark.asyncio
async def test_recent_includes_7d_aggregate_not_just_latest_day(async_client: AsyncClient, db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()

    # 3 días de historia para la misma campaña: el último día trae spend=0
    # (podría parecer "gratis" si solo se mirara el snapshot más reciente).
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today - datetime.timedelta(days=2),
        spend=50.0, conversions=5,
    ))
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=30.0, conversions=3,
    ))
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today,
        spend=0.0, conversions=0,
    ))
    await db_session.commit()

    headers = await _auth_headers(async_client, db_session, org, "kpi7d@example.com")
    res = await async_client.get(
        "/api/v1/campaigns/recent", headers={**headers, "X-Ad-Account-Id": acc.id}
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    campaign = body[0]

    # El snapshot más reciente (hoy) trae spend=0 — pero el agregado de 7d
    # debe reflejar la suma real de los 3 días.
    assert campaign["spend"] == 0.0
    assert campaign["spend_7d"] == 80.0
    assert campaign["conversations_7d"] == 8
    assert campaign["cost_per_conv_7d"] == 10.0
