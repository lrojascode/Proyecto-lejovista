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
from app.models.campaign_draft import CampaignDraft
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
    # Sin esto, el test hereda el allowlist del .env local del desarrollador
    # (F-2) y la cuenta de test queda simulada — fallaba solo fuera de CI.
    monkeypatch.setattr(settings, "WRITEBACK_ACCOUNT_ALLOWLIST", "")
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
async def test_task_create_adset_is_always_paused(ad_account, mocker):
    """Regla de oro del playbook (#3): todo se crea PAUSED, la activación la
    hace el humano manualmente en Ads Manager — nunca por API."""
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    run_in_thread(task_create_adset, _base_context(acc, campaign_id="mock_camp_1", campaign_name="X_MSJ_TEST"))

    _, _endpoint, payload = mock_call_api.call_args[0]
    assert payload["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_task_create_ad_is_always_paused(ad_account, mocker):
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )
    mocker.patch("app.services.notification_service.NotificationService.notify_job_status")

    context = _base_context(acc, adset_id="mock_adset_1", creative_id="mock_creative_1")
    run_in_thread(task_create_ad, context)

    _, _endpoint, payload = mock_call_api.call_args[0]
    assert payload["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_task_create_campaign_derives_brand_from_ad_account_name_not_hardcoded(ad_account, mocker):
    """La nomenclatura MARCA_TIPO_PRODUCTO_ZONA_MESAA (playbook §8) debe salir del
    nombre real de la cuenta conectada, no de un literal "MTV" para todos los
    tenants."""
    org, acc = ad_account  # acc.name == "Creation Acc"
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )

    context = run_in_thread(task_create_campaign, _base_context(acc))

    from app.workers.creation_tasks import _brand_code
    assert context["campaign_name"].startswith(f"{_brand_code(acc)}_MSJ_")
    assert "MTV" not in context["campaign_name"]


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


@pytest.mark.asyncio
async def test_task_handle_chain_error_notifies_real_tenant(ad_account, mocker):
    org, acc = ad_account
    mock_notify = mocker.patch(
        "app.services.notification_service.NotificationService.notify_job_status"
    )

    run_in_thread(
        task_handle_chain_error, None, Exception("boom"), None, "draft-42", "real-tenant-id"
    )

    mock_notify.assert_called_once_with(
        "real-tenant-id", "draft-42", "FAILED", event_type="campaign_creation"
    )


# --- Fase 1: persistencia real del CampaignDraft (antes el draft_id era un
# uuid suelto que no se guardaba en ningún lado y el frontend simulaba el
# polling con un setInterval que siempre terminaba en éxito) ---

@pytest.fixture
async def campaign_draft(ad_account, db_session: AsyncSession):
    org, acc = ad_account
    draft = CampaignDraft(
        organization_id=org.id,
        name="CableGPT",
        payload={"ad_account_id": acc.id, "product_name": "CableGPT"},
        status="PENDING_CREATION",
    )
    db_session.add(draft)
    await db_session.commit()
    return org, acc, draft


@pytest.mark.asyncio
async def test_task_create_campaign_persists_result_in_draft(campaign_draft, mocker, db_session):
    org, acc, draft = campaign_draft
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )

    run_in_thread(task_create_campaign, _base_context(acc, draft_id=draft.id))

    await db_session.refresh(draft)
    assert draft.payload["result"]["campaign_id"].startswith("mock_camp_")
    assert draft.name == draft.payload["result"]["campaign_name"]
    assert draft.status == "PENDING_CREATION"  # solo task_create_ad marca CREATED


@pytest.mark.asyncio
async def test_task_create_ad_marks_draft_created(campaign_draft, mocker, db_session):
    org, acc, draft = campaign_draft
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )
    mocker.patch("app.services.notification_service.NotificationService.notify_job_status")

    context = _base_context(
        acc, draft_id=draft.id, adset_id="mock_adset_1", creative_id="mock_creative_1"
    )
    run_in_thread(task_create_ad, context)

    await db_session.refresh(draft)
    assert draft.status == "CREATED"
    assert draft.payload["result"]["ad_id"].startswith("mock_ad_")


@pytest.mark.asyncio
async def test_task_create_ad_completes_ai_action_when_action_id_present(campaign_draft, mocker, db_session):
    """A-1: cuando la Saga viene de una AIAction (CREATE_CAMPAIGN aprobada),
    task_create_ad debe cerrar el ciclo también sobre esa acción, no solo
    sobre el CampaignDraft."""
    org, acc, draft = campaign_draft
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )
    mocker.patch("app.services.notification_service.NotificationService.notify_job_status")

    from app.models.ai_action import AIAction
    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id, action_type="CREATE_CAMPAIGN",
        risk_level="HIGH", title="Crear campaña", description="test",
        platform="META", parameters={}, planned_api_calls=[], status="EXECUTING",
    )
    db_session.add(action)
    await db_session.commit()

    context = _base_context(
        acc, draft_id=draft.id, campaign_id="mock_camp_1",
        adset_id="mock_adset_1", creative_id="mock_creative_1",
        action_id=action.id,
    )
    run_in_thread(task_create_ad, context)

    await db_session.refresh(action)
    assert action.status == "COMPLETED"
    assert action.execution_result["ad_id"].startswith("mock_ad_")
    assert action.execution_result["simulated"] is True


@pytest.mark.asyncio
async def test_task_handle_chain_error_fails_ai_action_when_action_id_present(campaign_draft, mocker, db_session):
    org, acc, draft = campaign_draft
    mocker.patch("app.services.notification_service.NotificationService.notify_job_status")

    from app.models.ai_action import AIAction
    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id, action_type="CREATE_CAMPAIGN",
        risk_level="HIGH", title="Crear campaña", description="test",
        platform="META", parameters={}, planned_api_calls=[], status="EXECUTING",
    )
    db_session.add(action)
    await db_session.commit()

    run_in_thread(
        task_handle_chain_error, None, Exception("Meta rechazó el creativo"), None,
        draft.id, "real-tenant-id", action.id,
    )

    await db_session.refresh(action)
    assert action.status == "FAILED"
    assert action.execution_result["error"] == "Meta rechazó el creativo"


@pytest.mark.asyncio
async def test_task_handle_chain_error_marks_draft_failed_with_message(campaign_draft, mocker, db_session):
    org, acc, draft = campaign_draft
    mocker.patch("app.services.notification_service.NotificationService.notify_job_status")

    run_in_thread(
        task_handle_chain_error, None, Exception("Meta rechazó el creativo"), None, draft.id, "real-tenant-id"
    )

    await db_session.refresh(draft)
    assert draft.status == "FAILED"
    assert draft.error_message == "Meta rechazó el creativo"


@pytest.mark.asyncio
async def test_merge_draft_result_no_op_when_draft_missing(ad_account, mocker):
    """No debe romper la Saga si el draft no existe (ej. borrado manual) — solo loguea."""
    org, acc = ad_account
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )

    context = run_in_thread(task_create_campaign, _base_context(acc, draft_id="nonexistent-draft"))

    assert context["campaign_id"].startswith("mock_camp_")


# --- Fase 6: soporte de video/UGC en la Saga (playbook §5 Paso 4) ---

@pytest.mark.asyncio
async def test_task_upload_image_skips_upload_when_video_provided(ad_account, mocker):
    org, acc = ad_account
    mock_repo = mocker.patch(
        "app.services.meta_asset_repository.MetaAssetRepository.get_or_upload_image", autospec=True
    )

    context = run_in_thread(
        task_upload_image,
        _base_context(
            acc, image_hash=None, image_url=None,
            video_id="vid_123", thumbnail_url="https://cdn.example/thumb.jpg",
        ),
    )

    mock_repo.assert_not_called()
    assert context["video_id"] == "vid_123"
    assert "image_hash" not in context or context.get("image_hash") is None


@pytest.mark.asyncio
async def test_task_create_creative_uses_video_data_when_video_provided(ad_account, mocker):
    import json
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"mock": True},
    )

    context = _base_context(
        acc, image_hash=None, video_id="vid_123", thumbnail_url="https://cdn.example/thumb.jpg",
    )
    run_in_thread(task_create_creative, context)

    _, _endpoint, payload = mock_call_api.call_args[0]
    spec = json.loads(payload["object_story_spec"])
    assert "video_data" in spec
    assert spec["video_data"]["video_id"] == "vid_123"
    assert spec["video_data"]["image_url"] == "https://cdn.example/thumb.jpg"


# --- Fase 6: error a mitad de la Saga — progreso parcial + estado FAILED coherente ---

# --- M-2: reglas de campaña por tenant (Organization.campaign_rules_overrides) ---

@pytest.mark.asyncio
async def test_task_create_campaign_uses_org_objective_override(ad_account, db_session, mocker):
    org, acc = ad_account
    org.campaign_rules_overrides = {"objective": "OUTCOME_TRAFFIC"}
    db_session.add(org)
    await db_session.commit()

    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    run_in_thread(task_create_campaign, _base_context(acc))

    _, _endpoint, payload = mock_call_api.call_args[0]
    assert payload["objective"] == "OUTCOME_TRAFFIC"


@pytest.mark.asyncio
async def test_task_create_campaign_uses_global_default_when_org_has_no_override(ad_account, mocker):
    """Sin overrides configurados, el comportamiento no cambia (M-2)."""
    org, acc = ad_account
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    run_in_thread(task_create_campaign, _base_context(acc))

    _, _endpoint, payload = mock_call_api.call_args[0]
    assert payload["objective"] == "OUTCOME_LEADS"  # TENANT_CAMPAIGN_RULES["objective"]


@pytest.mark.asyncio
async def test_task_create_adset_uses_org_optimization_goal_override(ad_account, db_session, mocker):
    org, acc = ad_account
    org.campaign_rules_overrides = {
        "optimization_goal": "LINK_CLICKS",
        "bid_strategy": "LOWEST_COST_WITH_BID_CAP",
    }
    db_session.add(org)
    await db_session.commit()

    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    context = _base_context(acc, campaign_id="mock_camp_1", campaign_name="X_MSJ_TEST")
    run_in_thread(task_create_adset, context)

    _, _endpoint, payload = mock_call_api.call_args[0]
    assert payload["optimization_goal"] == "LINK_CLICKS"
    assert payload["bid_strategy"] == "LOWEST_COST_WITH_BID_CAP"
    # Regla de oro del playbook (#3, no personalizable): sigue PAUSED.
    assert payload["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_task_create_adset_uses_org_geo_and_placements_override(ad_account, db_session, mocker):
    import json
    org, acc = ad_account
    org.campaign_rules_overrides = {
        "default_geo_region_key": "1234",
        "required_placements": ["facebook_marketplace"],
    }
    db_session.add(org)
    await db_session.commit()

    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )
    mock_call_api.return_value = {"mock": True}

    context = _base_context(acc, campaign_id="mock_camp_1", campaign_name="X_MSJ_TEST")
    run_in_thread(task_create_adset, context)

    _, _endpoint, payload = mock_call_api.call_args[0]
    targeting = json.loads(payload["targeting"])
    assert targeting["geo_locations"]["regions"] == [{"key": "1234"}]
    assert "facebook_marketplace" in targeting["facebook_positions"]


@pytest.mark.asyncio
async def test_saga_mid_chain_failure_preserves_partial_progress_and_marks_failed(
    campaign_draft, mocker, db_session
):
    """Simula el escenario real que dispara `on_error` en producción: el paso 2
    (crear campaña) tiene éxito y deja su progreso en el draft, pero el paso 3
    (crear adset) falla de forma permanente. El draft debe reflejar tanto el
    campaign_id ya creado (Meta no lo elimina — sigue PAUSED y sin adset, bajo
    riesgo) como el estado final FAILED con el motivo real, exactamente lo que
    haría Celery al invocar `task_handle_chain_error` tras la excepción."""
    org, acc = campaign_draft[0], campaign_draft[1]
    draft = campaign_draft[2]

    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )

    # Paso 2: crear campaña — éxito.
    mock_call_api.return_value = {"mock": True}
    context = run_in_thread(task_create_campaign, _base_context(acc, draft_id=draft.id))

    await db_session.refresh(draft)
    assert draft.payload["result"]["campaign_id"] == context["campaign_id"]
    assert draft.status == "PENDING_CREATION"  # aún no se marcó CREATED ni FAILED

    # Paso 3: crear adset — falla de forma permanente (ej. targeting inválido).
    mock_call_api.return_value = {"error": {"message": "Invalid parameter: targeting"}}
    with pytest.raises(PermanentMetaError) as exc_info:
        run_in_thread(task_create_adset, context)

    # Lo que Celery haría al llegar a on_error: invocar el callback con la excepción real.
    run_in_thread(
        task_handle_chain_error, None, exc_info.value, None, draft.id, context["tenant_id"]
    )

    await db_session.refresh(draft)
    assert draft.status == "FAILED"
    assert "targeting" in draft.error_message
    # El progreso del paso exitoso NO se pierde — queda trazado para diagnóstico.
    assert draft.payload["result"]["campaign_id"] == context["campaign_id"]
