import pytest
import concurrent.futures
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.workers.tasks import (
    schedule_all_syncs,
    schedule_all_analyses,
    sync_ad_account,
    run_ai_analysis,
    execute_action_task
)

def run_in_thread(func, *args, **kwargs):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(func, *args, **kwargs)
        return future.result()

class AsyncContextManagerMock:
    def __init__(self, value):
        self.value = value
    async def __aenter__(self):
        return self.value
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

@pytest.fixture(autouse=True)
def mock_db_session(mocker, db_session):
    mocker.patch("app.workers.tasks.async_session", lambda: AsyncContextManagerMock(db_session))

@pytest.fixture
async def setup_worker_data(init_db, db_session: AsyncSession):
    # Create orgs and ad accounts
    org_active = Organization(
        slug="org-active",
        name="Active Org",
        status="ACTIVE"
    )
    org_inactive = Organization(
        slug="org-inactive",
        name="Inactive Org",
        status="INACTIVE"
    )
    db_session.add(org_active)
    db_session.add(org_inactive)
    await db_session.flush()
    
    acc_active = AdAccount(
        organization_id=org_active.id,
        platform="META",
        external_account_id="111",
        name="Active Acc",
        status="ACTIVE"
    )
    acc_active.set_access_token("mock")
    
    acc_inactive = AdAccount(
        organization_id=org_active.id,
        platform="META",
        external_account_id="222",
        name="Inactive Acc",
        status="INACTIVE"
    )
    acc_inactive.set_access_token("mock")
    
    db_session.add(acc_active)
    db_session.add(acc_inactive)
    await db_session.flush()
    
    action = AIAction(
        ad_account_id=acc_active.id,
        organization_id=org_active.id,
        action_type="PAUSE_CAMPAIGN",
        risk_level="HIGH",
        title="Title",
        description="Desc",
        target_campaign_id="camp_1",
        platform="META",
        parameters={"old_status": "ACTIVE", "new_status": "PAUSED"},
        status="APPROVED",
        confidence=0.9
    )
    db_session.add(action)
    
    await db_session.commit()
    return org_active.id, acc_active.id, action.id

@pytest.mark.asyncio
async def test_schedule_all_syncs(setup_worker_data, mocker):
    org_id, acc_id, action_id = setup_worker_data
    mock_delay = mocker.patch("app.workers.tasks.sync_ad_account.delay")
    
    run_in_thread(schedule_all_syncs, "INCREMENTAL")
    
    mock_delay.assert_called_once_with(acc_id, "INCREMENTAL")

@pytest.mark.asyncio
async def test_schedule_all_analyses(setup_worker_data, mocker):
    org_id, acc_id, action_id = setup_worker_data
    mock_delay = mocker.patch("app.workers.tasks.run_ai_analysis.delay")
    
    run_in_thread(schedule_all_analyses)
    
    mock_delay.assert_called_once_with(org_id)

@pytest.mark.asyncio
async def test_sync_ad_account_task(setup_worker_data, mocker):
    org_id, acc_id, action_id = setup_worker_data
    
    mock_run_sync = mocker.patch(
        "app.services.meta_ads.sync.MetaAdsSyncService.run_sync",
        autospec=True
    )
    mock_run_analysis_delay = mocker.patch("app.workers.tasks.run_ai_analysis.delay")
    
    # We need to simulate that run_sync returns a SyncJob with COMPLETED status
    class MockJob:
        status = "COMPLETED"
        records_synced = 5
        
    mock_run_sync.return_value = MockJob()
    
    run_in_thread(sync_ad_account, acc_id, "INCREMENTAL")
    
    mock_run_sync.assert_called_once()
    mock_run_analysis_delay.assert_called_once_with(org_id)

@pytest.mark.asyncio
async def test_run_ai_analysis_task(setup_worker_data, mocker):
    org_id, acc_id, action_id = setup_worker_data
    mock_run_analysis = mocker.patch(
        "app.services.ai.engine.AIEngine.run_analysis",
        autospec=True
    )
    mock_run_analysis.return_value = {"insights_count": 2, "actions_proposed": 1}
    
    run_in_thread(run_ai_analysis, org_id)
    
    mock_run_analysis.assert_called_once()

@pytest.mark.asyncio
async def test_execute_action_task(setup_worker_data, mocker):
    org_id, acc_id, action_id = setup_worker_data
    mock_execute_action = mocker.patch(
        "app.services.ai.executor.AIExecutor.execute_action",
        autospec=True
    )
    # Simulate execute_action returning the updated action
    class MockAction:
        status = "COMPLETED"
        id = action_id
        action_type = "PAUSE_CAMPAIGN"
        
    mock_execute_action.return_value = MockAction()
    
    run_in_thread(execute_action_task, action_id)
    
    mock_execute_action.assert_called_once()
