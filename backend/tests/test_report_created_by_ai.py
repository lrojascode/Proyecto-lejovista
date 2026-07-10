"""5.4(d) (E-6): ReportBuilder marca las campañas cuyo origen fue una
propuesta de la IA (cruzando con CampaignLearningLog/CampaignDraft), no solo
las que el usuario armó a mano — antes el reporte no distinguía entre ambas."""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.services.report_builder import ReportBuilder


async def _make_org_and_account(db: AsyncSession, slug="ai-flag-org"):
    org = Organization(slug=slug, name="AI Flag Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_aiflag", name="AI Flag Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_campaign_created_via_draft_result_is_flagged(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    today = datetime.date.today()

    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-ai",
        campaign_name="Campaña de la IA", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=50.0, conversions=5,
    ))
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-manual",
        campaign_name="Campaña manual", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=40.0, conversions=4,
    ))

    draft = CampaignDraft(
        organization_id=org.id, name="Draft IA",
        payload={"result": {"campaign_id": "camp-ai"}}, status="CREATED",
    )
    db_session.add(draft)
    await db_session.flush()
    db_session.add(CampaignLearningLog(
        organization_id=org.id, ad_account_id=acc.id, draft_id=draft.id,
        product="Cable GPT", status="CREATED",
    ))
    await db_session.commit()

    report = await ReportBuilder(db_session).build(acc, window_days=7)

    ai_camp = next(c for c in report["campaigns"] if c["campaign_id"] == "camp-ai")
    manual_camp = next(c for c in report["campaigns"] if c["campaign_id"] == "camp-manual")
    assert ai_camp["created_by_ai"] is True
    assert manual_camp["created_by_ai"] is False


@pytest.mark.asyncio
async def test_campaign_flagged_via_evaluated_outcome(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="ai-flag-org-outcome")
    today = datetime.date.today()

    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="camp-evaluated",
        campaign_name="Campaña evaluada", status="ACTIVE", date=today - datetime.timedelta(days=1),
        spend=30.0, conversions=3,
    ))
    db_session.add(CampaignLearningLog(
        organization_id=org.id, ad_account_id=acc.id, draft_id=None,
        product="Cable GPT", status="EVALUATED",
        outcome_json={"verdict": "WIN", "campaign_id": "camp-evaluated"},
    ))
    await db_session.commit()

    report = await ReportBuilder(db_session).build(acc, window_days=7)

    camp = next(c for c in report["campaigns"] if c["campaign_id"] == "camp-evaluated")
    assert camp["created_by_ai"] is True
