"""5.1 (E-3): GET /reports/kpis-summary alimenta la fila de KPIs del
dashboard rejerarquizado — gasto/conversaciones/costo por conversación de
7d con Δ% vs 7d previos, alertas críticas activas y último sync."""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.models.sync_job import SyncJob
from app.models.user import User
from app.models.organization_user import OrganizationUser
from sqlalchemy import select


async def _make_org_and_account(db: AsyncSession, slug="kpisum-org"):
    org = Organization(slug=slug, name="KPI Summary Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_kpisum", name="Cuenta KPI Summary", status="ACTIVE",
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
async def test_kpis_summary_computes_deltas_alerts_and_last_sync(async_client: AsyncClient, db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()

    # Ventana actual (últimos 7 días): 100 de gasto, 10 conversiones.
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=100.0, conversions=10,
    ))
    # Ventana previa (días 8-14): 50 de gasto, 10 conversiones — el doble de spend.
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-1",
        campaign_name="Camp 1", status="ACTIVE", date=today - datetime.timedelta(days=10),
        spend=50.0, conversions=10,
    ))

    db_session.add(AIInsight(
        ad_account_id=acc.id, organization_id=org.id, level="CRITICAL",
        category="PERFORMANCE", title="CPL crítico", description="...",
        is_dismissed=False,
    ))
    db_session.add(AIInsight(
        ad_account_id=acc.id, organization_id=org.id, level="WARNING",
        category="SATURATION", title="Saturación", description="...",
        is_dismissed=False,
    ))
    # Descartado — no debe contar.
    db_session.add(AIInsight(
        ad_account_id=acc.id, organization_id=org.id, level="CRITICAL",
        category="PERFORMANCE", title="Ya resuelto", description="...",
        is_dismissed=True,
    ))

    sync_time = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
    db_session.add(SyncJob(
        ad_account_id=acc.id, organization_id=org.id, status="COMPLETED",
        sync_type="INCREMENTAL", completed_at=sync_time,
    ))
    await db_session.commit()

    headers = await _auth_headers(async_client, db_session, org, "kpisum@example.com")
    res = await async_client.get(
        "/api/v1/reports/kpis-summary", headers={**headers, "X-Ad-Account-Id": acc.id}
    )
    assert res.status_code == 200
    body = res.json()

    assert body["kpis_7d"]["spend"] == 100.0
    assert body["kpis_prev_7d"]["spend"] == 50.0
    assert body["deltas_pct"]["spend"] == 100.0  # 50 -> 100 es +100%
    assert body["critical_alerts_count"] == 1
    assert body["last_sync_at"] is not None


@pytest.mark.asyncio
async def test_kpis_summary_requires_active_ad_account(async_client: AsyncClient, db_session: AsyncSession):
    org = Organization(slug="kpisum-noaccount", name="No Account Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    await db_session.commit()

    headers = await _auth_headers(async_client, db_session, org, "kpisum-noacc@example.com")
    res = await async_client.get("/api/v1/reports/kpis-summary", headers=headers)
    assert res.status_code == 400
