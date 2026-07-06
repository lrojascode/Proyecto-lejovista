"""Tests de la Fase 0 del plan de creación de campañas (PLAN-CREACION-CAMPANAS.md):
las tareas de la Saga de creación deben usar el stack de escritura real
(MetaAPIRateLimiter, gateado por SIMULATE_META_WRITES), no el MetaApiClient
mockeado, y deben propagar el tenant_id real (no "mock-tenant-id").
"""
import concurrent.futures
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.meta_ads.exceptions import TransientMetaError, PermanentMetaError
from app.workers.creation_tasks import (
    task_upload_image,
    task_create_campaign,
    task_create_adset,
    task_create_creative,
    task_create_ad,
    task_handle_chain_error,
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
    mocker.patch("app.workers.creation_tasks.async_session", lambda: AsyncContextManagerMock(db_session))


@pytest.fixture
async def ad_account(init_db, db_session: AsyncSession):
    org = Organization(slug="creation-org", name="Creation Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()

    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Creation Acc", status="ACTIVE",
    )
    acc.set_access_token("mock-token")
    db_session.add(acc)
    await db_session.commit()
    return org, acc


def _base_context(acc: AdAccount, **overrides):
    ctx = {
        "draft_id": "draft-1",
        "tenant_id": "real-tenant-id",
        "ad_account_id": acc.id,
        "page_id": "page-1",
        "product_name": "CableGPT",
        "zone": "Surquillo",
        "budget": 200.0,
        "days": 5,
        "whatsapp_number": "51999888777",
        "image_url": None,
        "image_hash": "existing_hash",
        "copy_text": "Hook + beneficios + CTA",
    }
    ctx.update(overrides)
    return ctx


@pytest.mark.asyncio
async def test_task_upload_image_reuses_existing_hash(ad_account, mocker):
    org, acc = ad_account
    mock_repo = mocker.patch("app.workers.creation_tasks.MetaAssetRepository")

    context = run_in_thread(task_upload_image, _base_context(acc))

    assert context["image_hash"] == "existing_hash"
    mock_repo.assert_not_called()


@pytest.mark.asyncio
async def test_task_upload_image_uploads_via_real_repository(ad_account, mocker):
    """No debe usar el MetaAssetRepository mockeado de antes; debe pasar por el
    repositorio real (que a su vez respeta SIMULATE_META_WRITES)."""
    org, acc = ad_account
    mock_get_or_upload = mocker.patch(
        "app.services.meta_asset_repository.MetaAssetRepository.get_or_upload_image",
        autospec=True,
    )
    mock_get_or_upload.return_value = "hash_desde_meta_real"

    context = run_in_thread(
        task_upload_image, _base_context(acc, image_hash=None, image_url="https://x.com/img.jpg")
    )

    assert context["image_hash"] == "hash_desde_meta_real"
    mock_get_or_upload.assert_called_once()
    _, kwargs = mock_get_or_upload.call_args
    assert kwargs["ad_account_external_id"] == acc.external_account_id
    assert kwargs["access_token"] == "mock-token"


@pytest.mark.asyncio
async def test_task_create_campaign_uses_rate_limiter_not_meta_api_client(ad_account, mocker):
    """Fase 0: consolidar sobre MetaAPIRateLimiter (el stack real del executor),
    no MetaApiClient (el cliente v19 usado solo para validar)."""
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    context = run_in_thread(task_create_campaign, _base_context(acc))

    mock_call_api.assert_called_once()
    _, endpoint, payload = mock_call_api.call_args[0]
    kwargs = mock_call_api.call_args[1]
    assert endpoint == f"{acc.external_account_id}/campaigns"
    assert kwargs["method"] == "POST"
    assert payload["status"] == "PAUSED"  # TENANT_CAMPAIGN_RULES["default_status"]
    # Bajo simulación no hay id real de Meta -> se usa un id local con prefijo mock_.
    assert context["campaign_id"].startswith("mock_camp_")


@pytest.mark.asyncio
async def test_task_create_campaign_uses_real_id_when_writeback_enabled(ad_account, mocker, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "ENABLE_META_WRITEBACK", True)
    assert settings.SIMULATE_META_WRITES is False

    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"id": "120207114665560569"}

    context = run_in_thread(task_create_campaign, _base_context(acc))

    assert context["campaign_id"] == "120207114665560569"


@pytest.mark.asyncio
async def test_task_create_campaign_raises_transient_on_rate_limit_error(ad_account, mocker):
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"error": {"message": "Please reduce the rate at which you 429 too many"}}

    with pytest.raises(TransientMetaError):
        run_in_thread(task_create_campaign, _base_context(acc))


@pytest.mark.asyncio
async def test_task_create_campaign_raises_permanent_on_bad_request(ad_account, mocker):
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"error": {"message": "Invalid parameter"}}

    with pytest.raises(PermanentMetaError):
        run_in_thread(task_create_campaign, _base_context(acc))


@pytest.mark.asyncio
async def test_task_create_adset_includes_whatsapp_number_in_promoted_object(ad_account, mocker):
    import json
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    context = _base_context(acc, campaign_id="mock_camp_1", campaign_name="MTV_MSJ_TEST")
    result = run_in_thread(task_create_adset, context)

    _, endpoint, payload = mock_call_api.call_args[0]
    assert endpoint == f"{acc.external_account_id}/adsets"
    promoted_object = json.loads(payload["promoted_object"])
    assert promoted_object["whatsapp_phone_number"] == "51999888777"
    assert promoted_object["page_id"] == "page-1"
    assert payload["lifetime_budget"] == int(200.0 * 100)
    assert result["adset_id"].startswith("mock_adset_")


@pytest.mark.asyncio
async def test_task_create_creative_uses_creative_builder_payload(ad_account, mocker):
    import json
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    context = _base_context(acc)
    result = run_in_thread(task_create_creative, context)

    _, endpoint, payload = mock_call_api.call_args[0]
    assert endpoint == f"{acc.external_account_id}/adcreatives"
    spec = json.loads(payload["object_story_spec"])
    assert spec["page_id"] == "page-1"
    assert spec["link_data"]["image_hash"] == "existing_hash"
    assert result["creative_id"].startswith("mock_creative_")


@pytest.mark.asyncio
async def test_task_create_ad_notifies_with_real_tenant_id_not_mock(ad_account, mocker):
    org, acc = ad_account
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )
    mock_notify = mocker.patch(
        "app.services.notification_service.NotificationService.notify_job_status"
    )

    context = _base_context(acc, adset_id="mock_adset_1", creative_id="mock_creative_1")
    result = run_in_thread(task_create_ad, context)

    assert result["status"] == "COMPLETED"
    mock_notify.assert_called_once_with(
        "real-tenant-id", "draft-1", "READY", event_type="campaign_creation"
    )


def test_task_handle_chain_error_notifies_real_tenant(mocker):
    mock_notify = mocker.patch(
        "app.services.notification_service.NotificationService.notify_job_status"
    )

    task_handle_chain_error(None, Exception("boom"), None, "draft-42", "real-tenant-id")

    mock_notify.assert_called_once_with(
        "real-tenant-id", "draft-42", "FAILED", event_type="campaign_creation"
    )
