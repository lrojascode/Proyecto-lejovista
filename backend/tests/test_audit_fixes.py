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
