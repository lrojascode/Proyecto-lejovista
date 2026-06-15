import pytest
import datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.models.ai_action import AIAction

@pytest.fixture
def mock_celery(mocker):
    # Mocking Celery tasks to avoid trying to connect to a real Redis broker
    mock_sync = mocker.patch("app.api.v1.organizations.sync_ad_account")
    mock_sync_trigger = mocker.patch("app.api.v1.campaigns.sync_ad_account")
    mock_analysis_trigger = mocker.patch("app.api.v1.insights.run_ai_analysis")
    mock_execute = mocker.patch("app.api.v1.actions.execute_action_task")
    return {
        "sync_ad_account": mock_sync,
        "sync_trigger": mock_sync_trigger,
        "run_ai_analysis": mock_analysis_trigger,
        "execute_action_task": mock_execute
    }

@pytest.mark.asyncio
async def test_auth_flow_and_endpoints(async_client: AsyncClient):
    # --- ARRANGE & ACT: Signup ---
    signup_payload = {
        "email": "test@example.com",
        "password": "securepassword123",
        "full_name": "Test User",
        "organization_slug": "test-org",
        "organization_name": "Test Org LLC"
    }
    response = await async_client.post("/api/v1/auth/signup", json=signup_payload)
    
    # --- ASSERT ---
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["full_name"] == "Test User"
    assert data["user"]["role"] == "ADMIN"
    
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # --- ACT: Signup conflict ---
    response_conflict = await async_client.post("/api/v1/auth/signup", json=signup_payload)
    assert response_conflict.status_code == 400
    
    # --- ACT: Login ---
    login_payload = {
        "email": "test@example.com",
        "password": "securepassword123"
    }
    response_login = await async_client.post("/api/v1/auth/login", json=login_payload)
    assert response_login.status_code == 200
    assert "access_token" in response_login.json()
    
    # --- ACT: Login failure ---
    login_fail_payload = {
        "email": "test@example.com",
        "password": "wrongpassword"
    }
    response_login_fail = await async_client.post("/api/v1/auth/login", json=login_fail_payload)
    assert response_login_fail.status_code == 401

    # --- ACT: Get current user ---
    response_me = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response_me.status_code == 200
    assert response_me.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_organization_and_ad_accounts(async_client: AsyncClient, mock_celery):
    # --- ARRANGE: Signup user to get token and organization ---
    signup_payload = {
        "email": "org_test@example.com",
        "password": "securepassword123",
        "full_name": "Org Admin",
        "organization_slug": "org-test",
        "organization_name": "Org Test Corp"
    }
    signup_res = await async_client.post("/api/v1/auth/signup", json=signup_payload)
    token = signup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # --- ACT: GET organization/my ---
    org_res = await async_client.get("/api/v1/organizations/my", headers=headers)
    assert org_res.status_code == 200
    org_data = org_res.json()
    assert org_data["slug"] == "org-test"
    assert org_data["name"] == "Org Test Corp"
    
    # --- ACT: PUT organization/my ---
    update_payload = {
        "name": "Updated Org Name",
        "ai_model": "google/gemini-1.5-pro",
        "max_budget": 2500.0
    }
    update_res = await async_client.put("/api/v1/organizations/my", json=update_payload, headers=headers)
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["name"] == "Updated Org Name"
    assert updated_data["ai_model"] == "google/gemini-1.5-pro"
    assert updated_data["max_budget"] == 2500.0
    
    # --- ACT: POST connect ad account ---
    ad_acc_payload = {
        "platform": "META",
        "external_account_id": "1234567890",
        "name": "Meta Ads Account Test",
        "access_token": "mock_access_token",
        "refresh_token": "mock_refresh_token"
    }
    connect_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json=ad_acc_payload, headers=headers)
    assert connect_res.status_code == 200
    ad_acc_data = connect_res.json()
    assert ad_acc_data["name"] == "Meta Ads Account Test"
    assert ad_acc_data["external_account_id"] == "1234567890"
    
    # Assert Celery task was scheduled
    mock_celery["sync_ad_account"].delay.assert_called_once()
    
    # Connect same account again should fail
    connect_dup_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json=ad_acc_payload, headers=headers)
    assert connect_dup_res.status_code == 400

    # --- ACT: GET list ad accounts ---
    list_res = await async_client.get("/api/v1/organizations/my/ad-accounts", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1
    assert list_res.json()[0]["name"] == "Meta Ads Account Test"


@pytest.mark.asyncio
async def test_campaign_snapshots_and_sync_trigger(async_client: AsyncClient, mock_celery):
    # --- ARRANGE: Create user, org, and add ad account directly to DB ---
    signup_payload = {
        "email": "camp_test@example.com",
        "password": "securepassword123",
        "full_name": "Camp User",
        "organization_slug": "camp-test",
        "organization_name": "Camp Test Corp"
    }
    signup_res = await async_client.post("/api/v1/auth/signup", json=signup_payload)
    token = signup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # --- ACT: POST trigger manual sync without active accounts ---
    sync_trigger_res = await async_client.post("/api/v1/campaigns/sync-trigger", headers=headers)
    assert sync_trigger_res.status_code == 400 # No active ad accounts yet
    
    # Connect ad account
    ad_acc_payload = {
        "platform": "META",
        "external_account_id": "999888777",
        "name": "Campaign Ads Acc",
        "access_token": "mock_token"
    }
    await async_client.post("/api/v1/organizations/my/ad-accounts", json=ad_acc_payload, headers=headers)
    
    # Now trigger manual sync
    sync_trigger_success_res = await async_client.post("/api/v1/campaigns/sync-trigger", headers=headers)
    assert sync_trigger_success_res.status_code == 200
    assert "Sincronización manual programada" in sync_trigger_success_res.json()["message"]
    mock_celery["sync_trigger"].delay.assert_called_once()


@pytest.mark.asyncio
async def test_insights_and_actions(async_client: AsyncClient, mock_celery):
    # --- ARRANGE: Create user and org ---
    signup_payload = {
        "email": "insights_test@example.com",
        "password": "securepassword123",
        "full_name": "Insights User",
        "organization_slug": "insights-test",
        "organization_name": "Insights Test Corp"
    }
    signup_res = await async_client.post("/api/v1/auth/signup", json=signup_payload)
    user_data = signup_res.json()["user"]
    org_id = user_data["organization_id"]
    token = signup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Connect ad account to get account ID
    ad_acc_payload = {
        "platform": "META",
        "external_account_id": "55555555",
        "name": "Insights Account",
        "access_token": "mock_token"
    }
    acc_res = await async_client.post("/api/v1/organizations/my/ad-accounts", json=ad_acc_payload, headers=headers)
    ad_account_id = acc_res.json()["id"]

    # --- ACT: Trigger manual analysis ---
    analysis_trigger_res = await async_client.post("/api/v1/insights/analyze-trigger", headers=headers)
    assert analysis_trigger_res.status_code == 200
    mock_celery["run_ai_analysis"].delay.assert_called_once_with(org_id)
    
    # --- GET initial insights (should be empty) ---
    insights_res = await async_client.get("/api/v1/insights", headers=headers)
    assert insights_res.status_code == 200
    assert len(insights_res.json()) == 0
    
    # --- GET initial actions (should be empty) ---
    actions_res = await async_client.get("/api/v1/actions", headers=headers)
    assert actions_res.status_code == 200
    assert len(actions_res.json()) == 0


@pytest.mark.asyncio
async def test_narrative_brief_generation(async_client: AsyncClient, mock_celery):
    # --- ARRANGE: Create user and connect ad account ---
    signup_payload = {
        "email": "reports_test@example.com",
        "password": "securepassword123",
        "full_name": "Reports User",
        "organization_slug": "reports-test",
        "organization_name": "Reports Test Corp"
    }
    signup_res = await async_client.post("/api/v1/auth/signup", json=signup_payload)
    token = signup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Attempt report generation before connecting account - should fail
    report_fail_res = await async_client.get("/api/v1/reports/brief", headers=headers)
    assert report_fail_res.status_code == 400
    
    # Connect account
    ad_acc_payload = {
        "platform": "META",
        "external_account_id": "77777777",
        "name": "Reports Account",
        "access_token": "mock_token"
    }
    await async_client.post("/api/v1/organizations/my/ad-accounts", json=ad_acc_payload, headers=headers)
    
    # Try again. It should fail because there are no campaign snapshots synced
    report_no_data_res = await async_client.get("/api/v1/reports/brief", headers=headers)
    assert report_no_data_res.status_code == 400
    assert "No hay suficientes datos" in report_no_data_res.json()["detail"]
