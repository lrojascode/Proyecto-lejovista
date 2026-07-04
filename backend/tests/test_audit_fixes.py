"""Tests de los arreglos de la auditoría 2026.

Cubren comportamiento nuevo o corregido: auth en /sync (S-2), expiración de
tokens (S-6), narrativa sin cifras inventadas (AI-1), aviso de histórico
insuficiente (AI-3), unicidad de snapshots (D-1), presupuesto real en el planner
(AI-2) y marca simulado/real del write-back (AI-6/P-1).
"""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.models.ai_action import AIAction


async def _make_org_and_account(db: AsyncSession, slug="af-org"):
    org = Organization(slug=slug, name="Audit Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Audit Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


# --- S-2: /sync requiere autenticación ---
@pytest.mark.asyncio
async def test_sync_endpoint_requires_auth(async_client: AsyncClient):
    res = await async_client.post("/api/v1/sync/?ad_account_id=x&page_id=y")
    assert res.status_code == 401


# --- S-6: expiración de tokens de Meta ---
@pytest.mark.asyncio
async def test_meta_credential_expiry():
    from app.models.meta_credential import MetaCredential
    cred = MetaCredential(organization_id="o1")
    cred.set_access_token("some-token")
    assert cred.is_expired is False
    cred.token_expires_at = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    assert cred.is_expired is True


# --- AI-1: la narrativa mock deriva cifras de los datos reales ---
@pytest.mark.asyncio
async def test_mock_narrative_uses_real_numbers():
    from app.services.ai.llm import OpenRouterClient
    org = Organization(slug="n-org", name="N Org")
    acc = AdAccount(organization_id="o", platform="META", external_account_id="1", name="Acc")
    raw = {"c1": {"name": "Campania1", "spend": 100.0, "conversions": 50,
                  "impressions": 1000, "clicks": 20, "ctr": 2.0, "budget": 30.0}}
    text = OpenRouterClient()._generate_mock_narrative(org, acc, raw)
    assert "Campania1" in text
    assert "S/.2.00" in text          # CPL real = 100/50
    assert "S/.1.45" not in text      # ya no se inventan cifras
    assert "S/.12.00" not in text


# --- AI-3: aviso explícito de histórico insuficiente ---
@pytest.mark.asyncio
async def test_capa2_flags_insufficient_history(db_session: AsyncSession):
    from app.services.ai.statistics import Capa2Statistics
    org, acc = await _make_org_and_account(db_session, slug="c2-org")
    today = datetime.date.today()
    for d in range(3):  # solo 3 días (< 7)
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
            campaign_name="Camp 1", status="ACTIVE", spend=10.0, conversions=2,
            impressions=100, clicks=5, date=today - datetime.timedelta(days=d),
        ))
    await db_session.flush()
    insights = await Capa2Statistics(db_session).analyze(acc)
    assert any(i.title.startswith("Histórico insuficiente") for i in insights)


# --- D-1: unicidad (ad_account_id, campaign_id, date) ---
@pytest.mark.asyncio
async def test_snapshot_unique_constraint(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="uq-org")
    today = datetime.date.today()
    base = dict(ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
                campaign_name="Camp", status="ACTIVE", date=today)
    db_session.add(CampaignSnapshot(**base))
    await db_session.flush()
    db_session.add(CampaignSnapshot(**base))
    with pytest.raises(Exception):
        await db_session.flush()


# --- AI-2: el planner usa el presupuesto real del snapshot ---
@pytest.mark.asyncio
async def test_planner_uses_real_budget(db_session: AsyncSession):
    from app.services.ai.planner import AIPlanner
    org, acc = await _make_org_and_account(db_session, slug="pl-org")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Camp", status="ACTIVE", budget=80.0, spend=40.0,
        conversions=20, impressions=500, clicks=10, date=today,
    ))
    insight = AIInsight(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        level="INFO", category="PERFORMANCE",
        title="Excelente rendimiento: CPL a la baja",
        description="El CPL bajó; se sugiere ampliar presupuesto.",
        suggested_action_type="MODIFY_BUDGET",
    )
    db_session.add(insight)
    await db_session.flush()

    actions = await AIPlanner(db_session).plan_actions(acc, [insight])
    budget_actions = [a for a in actions if a.action_type == "MODIFY_BUDGET"]
    assert len(budget_actions) == 1
    assert budget_actions[0].parameters["old_budget"] == 80.0   # real, no hardcodeado
    assert budget_actions[0].parameters["new_budget"] == 100.0  # +25%


# --- AI-6 / P-1: el executor marca la ejecución como simulada (write-back off) ---
@pytest.mark.asyncio
async def test_executor_marks_simulated_when_writeback_disabled(db_session: AsyncSession):
    from app.services.ai.executor import AIExecutor
    from app.config import settings
    assert settings.SIMULATE_META_WRITES is True  # ENABLE_META_WRITEBACK=false en el entorno de test

    org, acc = await _make_org_and_account(db_session, slug="ex-org")
    action = AIAction(
        ad_account_id=acc.id, organization_id=org.id, action_type="PAUSE_CAMPAIGN",
        risk_level="HIGH", title="Pausar", description="Pausar campaña",
        target_campaign_id="camp_1", platform="META",
        parameters={"old_status": "ACTIVE", "new_status": "PAUSED"},
        status="APPROVED", confidence=0.9,
    )
    db_session.add(action)
    await db_session.flush()

    updated = await AIExecutor(db_session).execute_action(acc, action)
    assert updated.status == "COMPLETED"
    assert updated.execution_result.get("simulated") is True


# --- S-8: organización activa seleccionable (multi-tenant) ---
@pytest.mark.asyncio
async def test_user_active_org_property():
    from app.models.user import User
    from app.models.organization_user import OrganizationUser
    user = User(email="u@x.com", hashed_password="h", full_name="U")
    # Con dos membresías, por defecto usa la primera.
    ou1 = OrganizationUser(user_id="u", organization_id="org-1", role="ADMIN")
    ou2 = OrganizationUser(user_id="u", organization_id="org-2", role="VIEWER")
    from sqlalchemy.orm.attributes import set_committed_value
    set_committed_value(user, "organizations", [ou1, ou2])
    assert user.organization_id == "org-1"
    # Con tenant activo fijado, usa ese.
    user._active_org_id = "org-2"
    assert user.organization_id == "org-2"


@pytest.mark.asyncio
async def test_active_tenant_switch_via_header(async_client: AsyncClient, db_session: AsyncSession):
    from sqlalchemy import select
    from app.models.user import User
    from app.models.organization import Organization
    from app.models.organization_user import OrganizationUser

    signup = {
        "email": "switch@example.com", "password": "securepassword123",
        "full_name": "Switch User", "organization_slug": "sw-a", "organization_name": "Org A",
    }
    res = await async_client.post("/api/v1/auth/signup", json=signup)
    token = res.json()["access_token"]
    org_a_id = res.json()["user"]["organization_id"]

    # Añadir al usuario a una segunda organización.
    u = (await db_session.execute(select(User).where(User.email == signup["email"]))).scalar_one()
    org_b = Organization(slug="sw-b", name="Org B", status="ACTIVE")
    db_session.add(org_b)
    await db_session.flush()
    db_session.add(OrganizationUser(user_id=u.id, organization_id=org_b.id, role="ADMIN"))
    await db_session.commit()

    # Sin header: organización del token (A).
    me_a = await async_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_a.json()["organization_id"] == org_a_id

    # Con X-Tenant-Slug de la org B: cambia el tenant activo.
    me_b = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Slug": "sw-b"},
    )
    assert me_b.json()["organization_id"] == org_b.id


# --- S-9: refresh token emite un access token nuevo y usable ---
@pytest.mark.asyncio
async def test_refresh_token_flow(async_client: AsyncClient):
    signup = {
        "email": "refresh@example.com", "password": "securepassword123",
        "full_name": "Refresh User", "organization_slug": "rf-org", "organization_name": "RF Org",
    }
    res = await async_client.post("/api/v1/auth/signup", json=signup)
    body = res.json()
    assert body.get("refresh_token")  # signup ya entrega refresh token

    refresh_res = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert refresh_res.status_code == 200
    new_access = refresh_res.json()["access_token"]
    assert new_access and new_access != body["access_token"]

    # El nuevo access token funciona en un endpoint protegido.
    me = await async_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200
    assert me.json()["email"] == signup["email"]


@pytest.mark.asyncio
async def test_refresh_rejects_access_token(async_client: AsyncClient):
    # Un access token no debe servir como refresh token (type mismatch).
    signup = {
        "email": "rf2@example.com", "password": "securepassword123",
        "full_name": "U", "organization_slug": "rf2-org", "organization_name": "RF2",
    }
    res = await async_client.post("/api/v1/auth/signup", json=signup)
    access = res.json()["access_token"]
    bad = await async_client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert bad.status_code == 401


# --- P-2: el sync captura breakdowns de género, dispositivo y geografía ---
@pytest.mark.asyncio
async def test_sync_captures_extra_breakdowns(db_session: AsyncSession):
    from sqlalchemy import select
    from app.services.meta_ads.sync import MetaAdsSyncService

    org, acc = await _make_org_and_account(db_session, slug="p2-org")
    await db_session.commit()

    job = await MetaAdsSyncService(db_session).run_sync(acc, "INCREMENTAL")
    assert job.status == "COMPLETED"

    snap = (await db_session.execute(
        select(CampaignSnapshot).where(CampaignSnapshot.ad_account_id == acc.id).limit(1)
    )).scalar_one()
    assert set(snap.extra_breakdowns.keys()) == {"gender", "device", "region"}
    assert "male" in snap.extra_breakdowns["gender"]
    assert "mobile" in snap.extra_breakdowns["device"]
    assert "Lima" in snap.extra_breakdowns["region"]


# --- Regresión: snapshots antiguos (pre-P-2.7) con breakdowns NULL no rompen la respuesta ---
@pytest.mark.asyncio
async def test_snapshot_response_tolerates_null_breakdowns():
    from app.schemas.schemas import CampaignSnapshotResponse

    class Row:
        id = "1"; campaign_id = "c"; campaign_name = "n"; status = "ACTIVE"; budget = 0.0
        spend = 0.0; impressions = 0; clicks = 0; conversions = 0; cpl = 0.0; ctr = 0.0; frequency = 0.0
        placements_breakdown = None; demographics_breakdown = None; extra_breakdowns = None
        date = datetime.date.today()

    r = CampaignSnapshotResponse.model_validate(Row())
    assert r.extra_breakdowns == {}
    assert r.placements_breakdown == {}
    assert r.demographics_breakdown == {}


# --- P-2.1/P-2.3: el mock crea adsets, ads, creativos y audiencias ---
@pytest.mark.asyncio
async def test_mock_sync_creates_reporting_entities(db_session: AsyncSession):
    from sqlalchemy import select, func
    from app.services.meta_ads.sync import MetaAdsSyncService
    from app.models.adset_snapshot import AdsetSnapshot
    from app.models.ad_snapshot import AdSnapshot
    from app.models.meta_creative import MetaCreative
    from app.models.custom_audience import CustomAudience

    org, acc = await _make_org_and_account(db_session, slug="p21-org")
    await db_session.commit()

    job = await MetaAdsSyncService(db_session).run_sync(acc, "FULL")
    assert job.status == "COMPLETED"

    async def _count(model):
        return (await db_session.execute(
            select(func.count()).select_from(model).where(model.ad_account_id == acc.id)
        )).scalar()

    assert await _count(AdsetSnapshot) > 0
    assert await _count(AdSnapshot) > 0
    assert await _count(MetaCreative) == 3
    assert await _count(CustomAudience) == 3

    snap = (await db_session.execute(
        select(CampaignSnapshot).where(CampaignSnapshot.ad_account_id == acc.id).limit(1)
    )).scalar_one()
    assert snap.reach > 0
    assert snap.conversations == snap.conversions


# --- P-2.2: el sync real parsea adsets/ads/creativos/audiencias desde Meta ---
class _FakeLimiter:
    """Simula MetaAPIRateLimiter.call_api con respuestas canned de la Graph API."""
    def __init__(self):
        self.ad_account_id = None

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        d = "2026-07-02"
        if endpoint.endswith("/insights"):
            level = params.get("level")
            bd = params.get("breakdowns")
            msg = [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "20"}]
            if bd:
                # Desgloses: una fila por dimensión con la clave correspondiente.
                keys = {
                    "publisher_platform,platform_position": {"publisher_platform": "facebook", "platform_position": "feed"},
                    "age": {"age": "25-34"},
                    "gender": {"gender": "male"},
                    "impression_device": {"impression_device": "android_smartphone"},
                    "region": {"region": "Lima"},
                }.get(bd, {})
                return {"data": [{"campaign_id": "c1", "date_start": d, "spend": "50.0", "actions": msg, **keys}]}
            if level == "campaign":
                return {"data": [{"campaign_id": "c1", "campaign_name": "Camp 1", "date_start": d,
                                  "spend": "50.0", "impressions": "5000", "reach": "4000", "clicks": "90",
                                  "frequency": "1.25",
                                  "actions": msg}]}
            if level == "adset":
                return {"data": [{"adset_id": "as1", "adset_name": "Adset 1", "campaign_id": "c1", "date_start": d,
                                  "spend": "30.0", "impressions": "3000", "reach": "2500", "clicks": "55",
                                  "actions": [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "12"}]}]}
            if level == "ad":
                return {"data": [{"ad_id": "ad1", "ad_name": "Ad 1", "adset_id": "as1", "campaign_id": "c1", "date_start": d,
                                  "spend": "30.0", "impressions": "3000", "reach": "2500", "clicks": "55",
                                  "actions": [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "12"}]}]}
        if endpoint.endswith("/campaigns"):
            return {"data": [{"id": "c1", "name": "Camp 1", "status": "ACTIVE", "daily_budget": "7000"}]}
        if endpoint.endswith("/adsets"):
            return {"data": [{"id": "as1", "name": "Adset 1", "campaign_id": "c1",
                              "targeting": {"geo_locations": {"regions": [{"name": "Lima"}]},
                                            "age_min": 25, "age_max": 54,
                                            "publisher_platforms": ["facebook"],
                                            "targeting_automation": {"advantage_audience": 1}}}]}
        if endpoint.endswith("/ads"):
            return {"data": [{"id": "ad1", "creative": {"id": "cr1"}}]}
        if endpoint.endswith("/adimages"):
            return {"data": [{"hash": "h1", "name": "Imagen 1", "url": "http://x/1.jpg", "width": 1080, "height": 1080}]}
        if endpoint.endswith("/customaudiences"):
            return {"data": [{"id": "aud1", "name": "Audiencia 1", "subtype": "CUSTOM", "approximate_count": 1000}]}
        # info de cuenta
        return {"amount_spent": "10000", "spend_cap": "500000", "account_status": 1}


@pytest.mark.asyncio
async def test_real_sync_parses_meta_entities(db_session: AsyncSession):
    from sqlalchemy import select, func
    from app.services.meta_ads.sync import MetaAdsSyncService
    from app.models.adset_snapshot import AdsetSnapshot
    from app.models.ad_snapshot import AdSnapshot
    from app.models.meta_creative import MetaCreative
    from app.models.custom_audience import CustomAudience

    org, acc = await _make_org_and_account(db_session, slug="p22-org")
    acc.set_access_token("valid-token")
    await db_session.commit()

    n = await MetaAdsSyncService(db_session)._sync_real_data(acc, "INCREMENTAL", _FakeLimiter())
    assert n == 1  # 1 snapshot de campaña

    camp = (await db_session.execute(select(CampaignSnapshot).where(CampaignSnapshot.ad_account_id == acc.id))).scalar_one()
    assert camp.reach == 4000
    assert camp.conversations == 20   # desde el evento de mensajería
    # P-2.7: los desgloses reales quedan poblados en el snapshot.
    assert camp.placements_breakdown.get("facebook_feed", {}).get("conversions") == 20
    assert "25-34" in camp.demographics_breakdown
    assert "male" in camp.extra_breakdowns["gender"]
    assert "android_smartphone" in camp.extra_breakdowns["device"]
    assert "Lima" in camp.extra_breakdowns["region"]
    adset = (await db_session.execute(select(AdsetSnapshot).where(AdsetSnapshot.ad_account_id == acc.id))).scalar_one()
    assert adset.conversations == 12
    assert adset.targeting_summary["geo"] == ["Lima"]
    ad = (await db_session.execute(select(AdSnapshot).where(AdSnapshot.ad_account_id == acc.id))).scalar_one()
    assert ad.creative_id == "cr1"
    assert (await db_session.execute(select(func.count()).select_from(MetaCreative).where(MetaCreative.ad_account_id == acc.id))).scalar() == 1
    assert (await db_session.execute(select(func.count()).select_from(CustomAudience).where(CustomAudience.ad_account_id == acc.id))).scalar() == 1


@pytest.mark.asyncio
async def test_real_sync_marks_account_expired_on_190(db_session: AsyncSession):
    from app.services.meta_ads.sync import MetaAdsSyncService

    class ExpiredLimiter:
        ad_account_id = None
        async def call_api(self, endpoint, params, method="GET", access_token=""):
            return {"error": {"message": "Session has expired", "code": 190, "type": "OAuthException"}}

    org, acc = await _make_org_and_account(db_session, slug="exp-org")
    acc.set_access_token("some-token")
    await db_session.commit()

    with pytest.raises(Exception):
        await MetaAdsSyncService(db_session)._sync_real_data(acc, "INCREMENTAL", ExpiredLimiter())
    assert acc.status == "EXPIRED"


# --- P-2.4: el ReportBuilder arma todas las secciones desde la DB ---
@pytest.mark.asyncio
async def test_report_builder_full_sections(db_session: AsyncSession):
    from app.services.meta_ads.sync import MetaAdsSyncService
    from app.services.report_builder import ReportBuilder

    org, acc = await _make_org_and_account(db_session, slug="rb-org")
    await db_session.commit()
    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    report = await ReportBuilder(db_session).build(acc)
    assert report["kpis"]["total_spend"] > 0
    assert report["kpis"]["total_reach"] > 0
    assert len(report["campaigns"]) >= 1
    assert len(report["adsets"]) >= 1
    assert len(report["ads"]) >= 1
    assert len(report["creatives"]) == 3
    assert len(report["audiences"]) == 3
    assert report["segmentation"]["device"]  # dispositivos agregados
    assert report["segmentation"]["gender"]
    assert len(report["optimal_params"]) > 0
    assert "red" in report["alerts"] and "green" in report["alerts"]
    # ranking ordenado por costo/conversación ascendente
    costs = [c["cost_per_conv"] for c in report["campaigns"]]
    assert costs == sorted(costs)
