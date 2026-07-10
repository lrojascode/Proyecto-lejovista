"""5.3 (F-3.2): GET /reports/pdf genera un PDF real (WeasyPrint), no un
window.print() del navegador, a partir de los mismos datos de ReportBuilder."""
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


async def _make_org_and_account(db: AsyncSession, slug="pdf-org"):
    org = Organization(slug=slug, name="PDF Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_pdf", name="PDF Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


async def _auth_headers(async_client: AsyncClient, db: AsyncSession, org, acc, email: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123",
        "full_name": "Test User", "organization_slug": f"{org.slug}-personal",
        "organization_name": "Personal Org",
    })
    token = signup_res.json()["access_token"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    await db.commit()
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug, "X-Ad-Account-Id": acc.id}


@pytest.mark.asyncio
async def test_pdf_endpoint_returns_valid_pdf_bytes(async_client: AsyncClient, db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Campaña de prueba", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=100.0, conversions=10, reach=1000, impressions=5000, clicks=50,
    ))
    await db_session.commit()

    headers = await _auth_headers(async_client, db_session, org, acc, "pdf-test@example.com")
    res = await async_client.get("/api/v1/reports/pdf?window_days=30", headers=headers)

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_pdf_endpoint_rejects_invalid_window_days(async_client: AsyncClient, db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="pdf-org-badwindow")
    await db_session.commit()
    headers = await _auth_headers(async_client, db_session, org, acc, "pdf-window@example.com")

    res = await async_client.get("/api/v1/reports/pdf?window_days=15", headers=headers)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_pdf_endpoint_requires_active_ad_account(async_client: AsyncClient, db_session: AsyncSession):
    org = Organization(slug="pdf-org-noaccount", name="No Account Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    await db_session.commit()

    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": "pdf-noacc@example.com", "password": "securepassword123",
        "full_name": "Test User", "organization_slug": f"{org.slug}-personal",
        "organization_name": "Personal Org",
    })
    token = signup_res.json()["access_token"]
    user = (await db_session.execute(select(User).where(User.email == "pdf-noacc@example.com"))).scalar_one()
    db_session.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    await db_session.commit()

    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug}
    res = await async_client.get("/api/v1/reports/pdf", headers=headers)
    assert res.status_code == 400
