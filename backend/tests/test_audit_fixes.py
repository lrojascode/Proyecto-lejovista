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


# --- Routing multi-proveedor: OpenAI > Gemini directo > OpenRouter (fallback) ---
@pytest.mark.asyncio
async def test_llm_client_routes_to_openai_when_key_present(monkeypatch):
    from app.services.ai.llm import OpenRouterClient
    from app.config import settings
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    client = OpenRouterClient()
    assert client.api_key == "sk-test-openai"
    assert client.base_url == "https://api.openai.com/v1/chat/completions"
    assert client.is_gemini_direct is False


@pytest.mark.asyncio
async def test_llm_client_routes_to_gemini_direct_without_openai_key(monkeypatch):
    from app.services.ai.llm import OpenRouterClient
    from app.config import settings
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "gm-test-key")
    client = OpenRouterClient()
    assert client.api_key == "gm-test-key"
    assert client.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert client.is_gemini_direct is True


@pytest.mark.asyncio
async def test_llm_client_falls_back_to_openrouter_without_own_keys(monkeypatch):
    from app.services.ai.llm import OpenRouterClient
    from app.config import settings
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "or-test-key")
    client = OpenRouterClient()
    assert client.api_key == "or-test-key"
    assert client.base_url == "https://openrouter.ai/api/v1/chat/completions"
    assert client.is_gemini_direct is False


@pytest.mark.asyncio
async def test_llm_client_strips_google_prefix_for_gemini_direct(monkeypatch):
    """Gemini directo (endpoint compat OpenAI) no reconoce el prefijo 'google/' que
    usa OpenRouter para namespacing de modelos — hay que pelarlo antes de enviarlo."""
    import httpx
    from app.services.ai.llm import OpenRouterClient
    from app.config import settings

    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "gm-test-key")

    captured = {}

    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["model"] = json["model"]
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    org = Organization(slug="gm-org", name="Gemini Org", ai_model="google/gemini-2.5-flash")
    acc = AdAccount(organization_id="o", platform="META", external_account_id="1", name="Acc")
    await OpenRouterClient().analyze_campaigns(org, acc, "sin alertas", {})

    assert captured["model"] == "gemini-2.5-flash"  # sin el prefijo "google/"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


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


# --- AI-2: el planner usa el presupuesto real del snapshot y resuelve el adset real ---
@pytest.mark.asyncio
async def test_planner_uses_real_budget(db_session: AsyncSession):
    from app.services.ai.planner import AIPlanner
    from app.models.adset_snapshot import AdsetSnapshot
    org, acc = await _make_org_and_account(db_session, slug="pl-org")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Camp", status="ACTIVE", budget=80.0, spend=40.0,
        conversions=20, impressions=500, clicks=10, date=today,
    ))
    db_session.add(AdsetSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        adset_id="as1", name="Adset 1", date=today,
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
    # AI-2: la escritura real apunta al adset, no a la campaña.
    assert budget_actions[0].target_adset_id == "as1"
    assert budget_actions[0].planned_api_calls[0]["endpoint"] == "as1"


# --- AI-2: sin un adset único y sin ambigüedad, no se propone la acción a ciegas ---
@pytest.mark.asyncio
async def test_planner_skips_modify_budget_without_unambiguous_adset(db_session: AsyncSession):
    from app.services.ai.planner import AIPlanner
    from app.models.adset_snapshot import AdsetSnapshot
    org, acc = await _make_org_and_account(db_session, slug="pl-org-2")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Camp", status="ACTIVE", budget=80.0, spend=40.0,
        conversions=20, impressions=500, clicks=10, date=today,
    ))
    # Dos adsets vigentes para la misma campaña: ambiguo, no se debe resolver solo.
    db_session.add(AdsetSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        adset_id="as1", name="Adset 1", date=today,
    ))
    db_session.add(AdsetSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        adset_id="as2", name="Adset 2", date=today,
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
    assert [a for a in actions if a.action_type == "MODIFY_BUDGET"] == []


# --- AI-2: el modifier escribe sobre el adset, no sobre la campaña, para MODIFY_BUDGET ---
@pytest.mark.asyncio
async def test_modifier_targets_adset_for_modify_budget(db_session: AsyncSession):
    from app.services.meta_ads.modifier import MetaCampaignModifier
    org, acc = await _make_org_and_account(db_session, slug="mod-org")
    action = AIAction(
        ad_account_id=acc.id, organization_id=org.id, action_type="MODIFY_BUDGET",
        risk_level="MEDIUM", title="Subir presupuesto", description="desc",
        target_campaign_id="camp_1", target_adset_id="as1", platform="META",
        parameters={"old_budget": 80.0, "new_budget": 100.0},
        status="APPROVED", confidence=0.8,
    )
    db_session.add(action)
    await db_session.flush()

    await MetaCampaignModifier(db_session).modify(acc, action)
    # El backup (y por ende el target real de la escritura) usa el adset, no la campaña.
    assert action.parameters["backup_state"]["id"] == "as1"


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
            return {"data": [{"id": "aud1", "name": "Audiencia 1", "subtype": "CUSTOM",
                              "approximate_count_lower_bound": 1000, "approximate_count_upper_bound": 1200}]}
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
    # P-2.7: hallazgos y recomendaciones estructurados, derivados de los datos.
    assert len(report["findings"]) > 0
    assert len(report["recommendations"]) > 0
    assert all(isinstance(f, str) for f in report["findings"])
    # ranking ordenado por costo/conversación ascendente
    costs = [c["cost_per_conv"] for c in report["campaigns"]]
    assert costs == sorted(costs)


# --- D-3: archivado de snapshots viejos ---
@pytest.mark.asyncio
async def test_archive_old_snapshots_moves_old_rows(db_session: AsyncSession):
    from app.services.snapshot_archiver import archive_old_snapshots
    from app.models.adset_snapshot import AdsetSnapshot
    from app.models.ad_snapshot import AdSnapshot
    from app.models.snapshot_archive import (
        CampaignSnapshotArchive, AdsetSnapshotArchive, AdSnapshotArchive,
    )
    from sqlalchemy import select, func

    org, acc = await _make_org_and_account(db_session, slug="arch-org")
    today = datetime.date.today()
    old_date = today - datetime.timedelta(days=200)   # > retención por defecto (180)
    recent_date = today - datetime.timedelta(days=5)   # dentro de la retención

    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c-old",
        campaign_name="Vieja", status="ACTIVE", date=old_date,
    ))
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c-new",
        campaign_name="Reciente", status="ACTIVE", date=recent_date,
    ))
    db_session.add(AdsetSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c-old",
        adset_id="as-old", name="Adset viejo", date=old_date,
    ))
    db_session.add(AdSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c-old",
        adset_id="as-old", ad_id="ad-old", name="Ad viejo", date=old_date,
    ))
    await db_session.commit()

    counts = await archive_old_snapshots(db_session, retention_days=180)
    assert counts["campaign_snapshots"] == 1
    assert counts["adset_snapshots"] == 1
    assert counts["ad_snapshots"] == 1

    # La fila vieja se movió al archivo y desapareció de la tabla caliente.
    hot = (await db_session.execute(select(CampaignSnapshot).where(
        CampaignSnapshot.ad_account_id == acc.id))).scalars().all()
    assert [c.campaign_id for c in hot] == ["c-new"]

    archived = (await db_session.execute(select(CampaignSnapshotArchive).where(
        CampaignSnapshotArchive.ad_account_id == acc.id))).scalars().all()
    assert len(archived) == 1
    assert archived[0].campaign_id == "c-old"
    assert archived[0].organization_id == org.id

    assert (await db_session.execute(select(func.count()).select_from(AdsetSnapshotArchive))).scalar() == 1
    assert (await db_session.execute(select(func.count()).select_from(AdSnapshotArchive))).scalar() == 1


# --- Selector de modelo de IA: solo ofrece modelos del proveedor realmente activo ---
@pytest.mark.asyncio
async def test_ai_models_endpoint_reflects_active_provider(async_client: AsyncClient, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": "aimodels@example.com", "password": "securepassword123",
        "full_name": "AI Models User", "organization_slug": "ai-models-org",
        "organization_name": "AI Models Org",
    })
    token = signup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    res = await async_client.get("/api/v1/organizations/ai-models", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["active_provider"] == "openai"
    values = [m["value"] for m in data["models"]]
    assert values == ["gpt-5.4-mini", "gpt-5.4", "gpt-5.4-nano"]


# --- Historial de llamadas a la API de Meta en el dashboard ---
@pytest.mark.asyncio
async def test_api_call_history_scoped_to_tenant(async_client: AsyncClient, db_session: AsyncSession):
    from app.models.api_call_log import APICallLog

    org, acc = await _make_org_and_account(db_session, slug="hist-org")
    other_org, other_acc = await _make_org_and_account(db_session, slug="hist-org-2")
    db_session.add(APICallLog(
        ad_account_id=acc.id, method="GET", endpoint=f"{acc.external_account_id}/insights",
        status_code=200, response_preview="ok",
    ))
    db_session.add(APICallLog(
        ad_account_id=other_acc.id, method="GET", endpoint="otro-tenant/insights",
        status_code=200, response_preview="ok",
    ))
    await db_session.commit()

    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": "histuser@example.com", "password": "securepassword123",
        "full_name": "Hist User", "organization_slug": "hist-user-org",
        "organization_name": "Hist User Org",
    })
    # El usuario nuevo pertenece a su propia org, no a "hist-org" — para probar
    # el scoping usamos directamente el organization_id de la fixture en vez del
    # usuario recién creado.
    token = signup_res.json()["access_token"]

    # Ligamos manualmente al usuario a la organización de la fixture para probar el filtro.
    from app.models.user import User
    from sqlalchemy import select as sa_select
    user = (await db_session.execute(
        sa_select(User).where(User.email == "histuser@example.com")
    )).scalar_one()
    from app.models.organization_user import OrganizationUser
    db_session.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    await db_session.commit()

    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Slug": "hist-org"}
    res = await async_client.get("/api/v1/campaigns/api-call-history?limit=10", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["endpoint"] == f"{acc.external_account_id}/insights"
    assert data[0]["ad_account_name"] == acc.name
