"""Tests para el endpoint de detalle y drill-down de campaña individual (Fase 6.9 / Roadmap 12)."""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_snapshot import CampaignSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.ad_snapshot import AdSnapshot
from app.models.ai_action import AIAction


@pytest.mark.asyncio
async def test_get_campaign_details_endpoint(async_client: AsyncClient, db_session: AsyncSession, mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")

    # 1. Signup org
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "drilldown@test.com",
        "password": "securepassword123",
        "full_name": "Admin Drilldown",
        "organization_slug": "drilldown-org",
        "organization_name": "Drilldown Org",
    })
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": "drilldown-org"}

    # 2. Conectar cuenta
    acc_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json={
        "platform": "META",
        "name": "Cuenta Drilldown",
        "external_account_id": "act_888999",
        "access_token": "token_drilldown_123",
    }, headers=headers)
    acc_id = acc_res.json()["id"]
    from app.models.ad_account import AdAccount
    ad_acc = await db_session.get(AdAccount, acc_id)
    org_id = ad_acc.organization_id

    # 3. Crear snapshots de campaña
    snap1 = CampaignSnapshot(
        ad_account_id=acc_id,
        organization_id=org_id,
        campaign_id="camp_101",
        campaign_name="Campaña Zapatillas Pro",
        status="ACTIVE",
        budget=50.0,
        spend=45.0,
        impressions=1000,
        clicks=80,
        conversions=10,
        frequency=1.2,
        ctr=8.0,
        cpl=4.5,
        date=datetime.date(2026, 8, 18),
    )
    snap2 = CampaignSnapshot(
        ad_account_id=acc_id,
        organization_id=org_id,
        campaign_id="camp_101",
        campaign_name="Campaña Zapatillas Pro",
        status="ACTIVE",
        budget=50.0,
        spend=55.0,
        impressions=1200,
        clicks=95,
        conversions=12,
        frequency=1.3,
        ctr=7.9,
        cpl=4.58,
        date=datetime.date(2026, 8, 19),
    )
    db_session.add_all([snap1, snap2])

    # 4. Crear snapshot de AdSet
    adset = AdsetSnapshot(
        ad_account_id=acc_id,
        organization_id=org_id,
        campaign_id="camp_101",
        adset_id="as_201",
        name="AdSet Lima 18-35",
        budget=50.0,
        spend=100.0,
        impressions=2200,
        clicks=175,
        conversions=22,
        cpl=4.54,
        ctr=7.95,
        targeting_summary={"age_range": "18-35", "geo": "Lima"},
        date=datetime.date(2026, 8, 19),
    )
    db_session.add(adset)

    # 5. Crear snapshot de Ad
    ad = AdSnapshot(
        ad_account_id=acc_id,
        organization_id=org_id,
        campaign_id="camp_101",
        adset_id="as_201",
        ad_id="ad_301",
        name="Anuncio Zapatillas Running",
        creative_title="Las mejores zapatillas",
        creative_body="Comodidad y durabilidad para tus carreras",
        creative_cta_type="WHATSAPP_MESSAGE",
        spend=100.0,
        impressions=2200,
        clicks=175,
        conversions=22,
        cpl=4.54,
        ctr=7.95,
        date=datetime.date(2026, 8, 19),
    )
    db_session.add(ad)

    # 6. Crear acción asociada
    action = AIAction(
        ad_account_id=acc_id,
        organization_id=org_id,
        action_type="MODIFY_BUDGET",
        risk_level="LOW",
        title="Aumentar presupuesto 15%",
        description="Rendimiento sobresaliente con CPL bajo",
        target_campaign_id="camp_101",
        status="APPROVED",
    )
    db_session.add(action)
    await db_session.commit()

    # 7. Consultar endpoint
    details_res = await async_client.get("/api/v1/campaigns/camp_101/details", headers=headers)
    assert details_res.status_code == 200
    data = details_res.json()

    assert data["campaign_id"] == "camp_101"
    assert data["campaign_name"] == "Campaña Zapatillas Pro"
    assert data["total_spend"] == 100.0
    assert data["total_conversions"] == 22
    assert "888999" in data["ads_manager_url"]
    assert "camp_101" in data["ads_manager_url"]

    # Verificar AdSets y Ads
    assert len(data["adsets"]) == 1
    assert data["adsets"][0]["adset_id"] == "as_201"
    assert data["adsets"][0]["name"] == "AdSet Lima 18-35"

    assert len(data["ads"]) == 1
    assert data["ads"][0]["ad_id"] == "ad_301"
    assert data["ads"][0]["creative_title"] == "Las mejores zapatillas"

    # Verificar Acciones
    assert len(data["actions"]) == 1
    assert data["actions"][0]["title"] == "Aumentar presupuesto 15%"
