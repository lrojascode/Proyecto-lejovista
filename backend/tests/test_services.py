import pytest
import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.models.ai_action import AIAction
from app.services.meta_ads.sync import MetaAdsSyncService
from app.services.ai.engine import AIEngine
from app.services.ai.planner import AIPlanner
from app.services.ai.executor import AIExecutor

@pytest.fixture
async def setup_test_data(init_db, db_session: AsyncSession):
    # Setup organization and ad account
    org = Organization(
        slug="test-services-org",
        name="Services Test Org",
        ai_model="google/gemini-2.5-flash",
        max_budget=2000.0,
        status="ACTIVE"
    )
    db_session.add(org)
    await db_session.flush()
    
    ad_acc = AdAccount(
        organization_id=org.id,
        platform="META",
        external_account_id="12345",
        name="Services Test Account",
        status="ACTIVE"
    )
    ad_acc.set_access_token("mock_access_token")
    db_session.add(ad_acc)
    await db_session.commit()
    return org.id, ad_acc.id

@pytest.mark.asyncio
async def test_meta_ads_sync_service(setup_test_data, db_session: AsyncSession):
    org_id, ad_acc_id = setup_test_data
    
    ad_acc = await db_session.get(AdAccount, ad_acc_id)
    sync_service = MetaAdsSyncService(db_session)
    
    # Act: Run FULL sync mock
    job = await sync_service.run_sync(ad_acc, "FULL")
    
    # Assert job completion
    assert job.status == "COMPLETED"
    assert job.records_synced > 0
    assert ad_acc.spend_cap > 0
    assert ad_acc.status == "ACTIVE"
    
    # Check database snapshots were created
    q = select(CampaignSnapshot).where(CampaignSnapshot.ad_account_id == ad_acc_id)
    res = await db_session.execute(q)
    snapshots = res.scalars().all()
    assert len(snapshots) > 0

@pytest.mark.asyncio
async def test_ai_engine_analysis_and_planning(setup_test_data, db_session: AsyncSession):
    org_id, ad_acc_id = setup_test_data
    
    ad_acc = await db_session.get(AdAccount, ad_acc_id)
    
    # Arrange: create historical snapshots to trigger rules and statistics
    today = datetime.date.today()
    # Campaign 1: High CPL (failing)
    # Campaign 2: High Frequency (saturated)
    for i in range(10):
        date_val = today - datetime.timedelta(days=i)
        # Failing Campaign (S/. 15.0 CPL, 0 conversions last 2 days)
        snap1 = CampaignSnapshot(
            ad_account_id=ad_acc_id,
            organization_id=org_id,
            campaign_id="camp_failing",
            campaign_name="Failing Campaign",
            status="ACTIVE",
            budget=50.0,
            spend=50.0,
            impressions=1000,
            clicks=10,
            conversions=0 if i <= 1 else 3,
            cpl=50.0 if i <= 1 else 16.6,
            ctr=1.0,
            frequency=1.0 + (i * 0.05),
            placements_breakdown={},
            demographics_breakdown={},
            date=date_val
        )
        # Saturated Campaign (frequency > 3.0)
        snap2 = CampaignSnapshot(
            ad_account_id=ad_acc_id,
            organization_id=org_id,
            campaign_id="camp_saturated",
            campaign_name="Saturated Campaign",
            status="ACTIVE",
            budget=40.0,
            spend=40.0,
            impressions=5000,
            clicks=15,
            conversions=5,
            cpl=8.0,
            ctr=0.3,
            frequency=3.5,
            placements_breakdown={},
            demographics_breakdown={},
            date=date_val
        )
        db_session.add(snap1)
        db_session.add(snap2)
        
    await db_session.commit()
    
    # Act: Run AI Engine analysis
    engine = AIEngine(db_session)
    result = await engine.run_analysis(org_id)
    
    # Assert analysis execution returned insights and proposed actions
    assert result["insights_count"] > 0
    assert result["actions_proposed"] > 0
    assert "html_brief" in result
    
    # Check database records
    q_insights = select(AIInsight).where(AIInsight.organization_id == org_id)
    res_insights = await db_session.execute(q_insights)
    insights = res_insights.scalars().all()
    assert len(insights) > 0
    
    q_actions = select(AIAction).where(AIAction.organization_id == org_id)
    res_actions = await db_session.execute(q_actions)
    actions = res_actions.scalars().all()
    assert len(actions) > 0

    # AI-4: junto al modelo, se registra qué proveedor real sirvió la narrativa.
    await db_session.refresh(ad_acc)
    assert ad_acc.narrative_provider in ("openai", "gemini_direct", "openrouter")

@pytest.mark.asyncio
async def test_ai_executor_and_modifier(setup_test_data, db_session: AsyncSession):
    org_id, ad_acc_id = setup_test_data
    
    ad_acc = await db_session.get(AdAccount, ad_acc_id)
    
    # Arrange: Create proposed action
    action = AIAction(
        ad_account_id=ad_acc_id,
        organization_id=org_id,
        action_type="PAUSE_CAMPAIGN",
        risk_level="HIGH",
        title="Pause Inefficient Campaign",
        description="Pause failing campaign description",
        target_campaign_id="camp_failing",
        platform="META",
        parameters={"old_status": "ACTIVE", "new_status": "PAUSED"},
        status="APPROVED", # Set approved so it can be executed
        confidence=0.9
    )
    db_session.add(action)
    await db_session.commit()
    
    # Act: Execute action
    executor = AIExecutor(db_session)
    updated_action = await executor.execute_action(ad_acc, action)
    
    # Assert action completed successfully
    assert updated_action.status == "COMPLETED"
    assert updated_action.execution_result is not None
    assert "mock" in updated_action.execution_result or updated_action.execution_result.get("status") == "PAUSED"
