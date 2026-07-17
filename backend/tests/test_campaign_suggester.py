"""Tests de la Fase 3A del plan de creación de campañas: CampaignSuggester
deriva parámetros estructurales (objetivo, presupuesto, placements a excluir,
edad/dispositivo óptimos) de datos reales — nunca inventa cifras (AI-1)."""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.services.campaign_suggester import CampaignSuggester


async def _make_org_and_account(db: AsyncSession, slug="suggest-org"):
    org = Organization(slug=slug, name="Suggest Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Suggest Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_suggest_with_history_uses_real_winning_campaign(db_session: AsyncSession):
    from app.services.meta_ads.sync import MetaAdsSyncService
    org, acc = await _make_org_and_account(db_session, slug="suggest-with-history")
    await db_session.commit()
    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["has_history"] is True
    assert suggestion["winning_campaign"] is not None
    # La campaña "reels_better" del mock (camp_cable_gpt) es la más eficiente.
    assert "CABLE_GPT" in suggestion["winning_campaign"]["name"]
    assert suggestion["objective"] == "OUTCOME_LEADS"
    assert suggestion["destination_type"] == "WHATSAPP"
    assert suggestion["suggested_budget"] > 0
    assert any("más eficiente" in e for e in suggestion["evidence"])

    # Fase 6b: copy real del anuncio ganador, capturado por el sync (antes solo
    # se guardaba la imagen — nunca el texto).
    assert suggestion["winning_ad_copy"] is not None
    assert "Cable GPT" in suggestion["winning_ad_copy"]["title"]
    assert suggestion["winning_ad_copy"]["cta_type"] == "WHATSAPP_MESSAGE"
    assert any("Copy real capturado" in e for e in suggestion["evidence"])


@pytest.mark.asyncio
async def test_suggest_without_history_uses_documented_defaults(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="suggest-no-history")
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["has_history"] is False
    assert suggestion["winning_campaign"] is None
    assert suggestion["winning_ad_copy"] is None
    assert suggestion["suggested_budget"] == 100.0  # default documentado, no derivado
    assert any("no se derivan de datos reales" in e.lower() for e in suggestion["evidence"])


@pytest.mark.asyncio
async def test_suggest_winning_ad_copy_ignores_ads_without_conversions(db_session: AsyncSession):
    """Un anuncio con copy real pero sin conversiones no debe usarse como
    'ganador' — solo el más barato ENTRE los que sí convirtieron."""
    from app.models.ad_snapshot import AdSnapshot
    org, acc = await _make_org_and_account(db_session, slug="suggest-ad-copy")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Winner", status="ACTIVE", budget=50.0, spend=50.0,
        conversions=25, impressions=1000, clicks=20, date=today,
    ))
    db_session.add(AdSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        adset_id="as1", ad_id="ad_no_conv", name="Sin conversiones",
        spend=10.0, conversions=0, conversations=0, date=today,
        creative_title="No debería aparecer", creative_body="Copy sin resultados",
    ))
    db_session.add(AdSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        adset_id="as1", ad_id="ad_winner", name="Ganador",
        spend=40.0, conversions=25, conversations=25, date=today,
        creative_title="El copy que sí funcionó", creative_body="Cuerpo real ganador",
        creative_cta_type="WHATSAPP_MESSAGE",
    ))
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["winning_ad_copy"]["title"] == "El copy que sí funcionó"
    assert suggestion["winning_ad_copy"]["body"] == "Cuerpo real ganador"


@pytest.mark.asyncio
async def test_suggest_excludes_placements_with_spend_and_zero_conversions(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="suggest-exclude")
    today = datetime.date.today()
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="Winner", status="ACTIVE", budget=50.0, spend=50.0,
        conversions=25, impressions=1000, clicks=20, date=today,
        placements_breakdown={
            "facebook_feed": {"spend": 30.0, "conversions": 25},
            "audience_network": {"spend": 20.0, "conversions": 0},
        },
    ))
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["excluded_placements"] == ["audience_network"]
    assert any("audience network" in e for e in suggestion["evidence"])


@pytest.mark.asyncio
async def test_suggest_respects_explicit_budget_hint(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="suggest-budget-hint")
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc, budget_hint=350.0)

    assert suggestion["suggested_budget"] == 350.0


@pytest.mark.asyncio
async def test_suggest_uses_org_campaign_rules_override(db_session: AsyncSession):
    """M-2: una org con `campaign_rules_overrides` configurado recibe sus
    propios objective/optimization_goal/destination_type, no los globales."""
    org, acc = await _make_org_and_account(db_session, slug="suggest-org-override")
    org.campaign_rules_overrides = {
        "objective": "OUTCOME_TRAFFIC",
        "optimization_goal": "LINK_CLICKS",
        "destination_type": "WEBSITE",
    }
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["objective"] == "OUTCOME_TRAFFIC"
    assert suggestion["optimization_goal"] == "LINK_CLICKS"
    assert suggestion["destination_type"] == "WEBSITE"


# ═══ El eslabón del aprendizaje: los resultados propios cambian la PROPUESTA ═══
# Antes los veredictos solo alimentaban el texto explicativo; los parámetros
# salían siempre del histórico, idénticos con 5 victorias o con 5 derrotas.

async def _seed_evaluated_log(db: AsyncSession, org, acc, *, verdict, cost, params=None,
                              created_at=None):
    """Un CampaignLearningLog ya evaluado por el outcome loop."""
    from app.models.campaign_learning_log import CampaignLearningLog
    log = CampaignLearningLog(
        organization_id=org.id, ad_account_id=acc.id,
        params_json=params or {}, analysis_json={}, maturity_json={},
        status="EVALUATED",
        outcome_json={"verdict": verdict, "cost_per_conv": cost, "reason": "test"},
        created_at=created_at or datetime.datetime.utcnow(),
    )
    db.add(log)
    await db.flush()
    return log


@pytest.mark.asyncio
async def test_budget_is_calibrated_with_own_measured_cost(db_session: AsyncSession):
    """R1: el presupuesto pasa a salir del costo REAL logrado por la IA, no del
    mejor caso histórico de la cuenta."""
    from app.services.meta_ads.sync import MetaAdsSyncService
    org, acc = await _make_org_and_account(db_session, slug="suggest-calibrated")
    await db_session.commit()
    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    sin_outcomes = await CampaignSuggester(db_session).suggest(acc)

    # La IA logró de verdad S/.6 por conversación (peor que el récord histórico).
    await _seed_evaluated_log(db_session, org, acc, verdict="NEUTRAL", cost=6.0)
    await _seed_evaluated_log(db_session, org, acc, verdict="WIN", cost=6.0)
    await db_session.commit()

    con_outcomes = await CampaignSuggester(db_session).suggest(acc)

    # 20 conversaciones al costo real medido, no al mínimo histórico.
    assert con_outcomes["suggested_budget"] == 120.0
    assert con_outcomes["suggested_budget"] != sin_outcomes["suggested_budget"]
    assert any("costo real que la IA ha logrado" in e for e in con_outcomes["evidence"])


@pytest.mark.asyncio
async def test_budget_unchanged_without_own_results(db_session: AsyncSession):
    """No-regresión: una cuenta sin resultados propios evaluados se comporta
    exactamente igual que antes de este cambio."""
    from app.services.meta_ads.sync import MetaAdsSyncService
    org, acc = await _make_org_and_account(db_session, slug="suggest-no-outcomes")
    await db_session.commit()
    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    report = await __import__("app.services.report_cache", fromlist=["get_report"]).get_report(db_session, acc)
    costs = [c["cost_per_conv"] for c in report["campaigns"] if c["conversations"] > 0]
    assert suggestion["suggested_budget"] == round(min(costs) * 20, 2)
    assert not any("costo real que la IA ha logrado" in e for e in suggestion["evidence"])


async def _seed_stable_history(db_session, org, acc):
    """3 campañas ganadoras con costo por conversación estable (CV ≤ 0.25) — la
    otra condición que assess_maturity exige para converger. Los datos mock del
    sync tienen CPL volátil a propósito, así que no sirven aquí."""
    today = datetime.date.today()
    for i, (cid, spend, conv) in enumerate([
        ("s1", 40.0, 20), ("s2", 42.0, 20), ("s3", 38.0, 20),
    ]):
        db_session.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id=cid,
            campaign_name=f"Estable {i}", status="ACTIVE", budget=50.0, spend=spend,
            conversions=conv, conversations=conv, impressions=1000, clicks=20, date=today,
        ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_converged_replicates_own_winning_config(db_session: AsyncSession):
    """R2: al converger, dispositivo/edad/placements salen del WIN propio — no
    se recalculan del histórico general. Es lo que la UI ya prometía."""
    org, acc = await _make_org_and_account(db_session, slug="suggest-converged")
    await _seed_stable_history(db_session, org, acc)

    # 2 WIN propios (requisito de convergencia) con una config que NO saldría
    # del histórico, para poder distinguir de dónde vino.
    ganadora = {
        "best_device": "desktop_ganador",
        "best_age_range": "55-64",
        "excluded_placements": ["facebook_marketplace"],
    }
    now = datetime.datetime.utcnow()
    await _seed_evaluated_log(db_session, org, acc, verdict="WIN", cost=2.0,
                              params={"best_device": "win_viejo", "best_age_range": "18-24"},
                              created_at=now - datetime.timedelta(days=2))
    await _seed_evaluated_log(db_session, org, acc, verdict="WIN", cost=2.0,
                              params=ganadora, created_at=now)
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["maturity"]["converged"] is True
    # Del WIN propio MÁS RECIENTE, no del histórico ni del WIN viejo.
    assert suggestion["best_device"] == "desktop_ganador"
    assert suggestion["best_age_range"] == "55-64"
    assert suggestion["excluded_placements"] == ["facebook_marketplace"]
    assert any("Modo optimizado" in e for e in suggestion["evidence"])
    assert "replico la configuración exacta" in suggestion["analysis"]["summary"]


@pytest.mark.asyncio
async def test_not_converged_keeps_history_derived_config(db_session: AsyncSession):
    """Sin convergencia (1 solo WIN) NO se replica: sigue el comportamiento de
    siempre, derivado del histórico."""
    org, acc = await _make_org_and_account(db_session, slug="suggest-not-converged")
    await _seed_stable_history(db_session, org, acc)

    base = await CampaignSuggester(db_session).suggest(acc)

    await _seed_evaluated_log(db_session, org, acc, verdict="WIN", cost=2.0,
                              params={"best_device": "no_deberia_usarse"})
    await db_session.commit()

    suggestion = await CampaignSuggester(db_session).suggest(acc)

    assert suggestion["maturity"]["converged"] is False
    assert suggestion["best_device"] == base["best_device"]
    assert suggestion["best_device"] != "no_deberia_usarse"
