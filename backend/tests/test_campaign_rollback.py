"""Compensación de la Saga: si un paso posterior falla, la campaña ya creada en
Meta se borra en vez de quedar huérfana (vacía y pausada) en la cuenta del
cliente.

Incidente 2026-07-17: tres intentos de depuración dejaron tres campañas vacías
en la cuenta de Agencia Kibo, que hubo que borrar a mano. La Saga escribe en 4
pasos encadenados pero le faltaba la pata de compensación del patrón.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.workers.creation_tasks import _rollback_campaign


async def _make_draft(db: AsyncSession, slug: str, result: dict) -> tuple[AdAccount, CampaignDraft]:
    org = Organization(slug=slug, name="Rollback Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_rb", name="Rollback Acc", status="ACTIVE",
    )
    acc.set_access_token("tok")
    db.add(acc)
    await db.flush()

    draft = CampaignDraft(
        organization_id=org.id, name="Producto",
        payload={"ad_account_id": acc.id, "result": result},
        status="FAILED",
    )
    db.add(draft)
    await db.commit()
    return acc, draft


@pytest.mark.asyncio
async def test_rollback_deletes_orphan_campaign(db_session: AsyncSession, mocker):
    acc, draft = await _make_draft(db_session, "rb-org", {"campaign_id": "120251785657800237"})
    mock_call = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"success": True},
    )

    await _rollback_campaign(db_session, draft.id)

    mock_call.assert_called_once()
    _, endpoint, _params = mock_call.call_args[0]
    kwargs = mock_call.call_args[1]
    assert endpoint == "120251785657800237"
    assert kwargs["method"] == "DELETE"

    await db_session.refresh(draft)
    assert draft.payload["result"]["rolled_back_campaign_id"] == "120251785657800237"


@pytest.mark.asyncio
async def test_rollback_skips_when_campaign_never_created(db_session: AsyncSession, mocker):
    """Si la Saga falló antes del paso 2 no hay nada que compensar."""
    acc, draft = await _make_draft(db_session, "rb-org-nocamp", {"image_hash": "h1"})
    mock_call = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )

    await _rollback_campaign(db_session, draft.id)

    mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_skips_simulated_campaign(db_session: AsyncSession, mocker):
    """Una campaña simulada (mock_*) no existe en Meta: no se intenta borrarla."""
    acc, draft = await _make_draft(db_session, "rb-org-mock", {"campaign_id": "mock_camp_123"})
    mock_call = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )

    await _rollback_campaign(db_session, draft.id)

    mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_skips_when_saga_completed(db_session: AsyncSession, mocker):
    """Si el anuncio llegó a crearse, la Saga terminó bien — jamás borrar."""
    acc, draft = await _make_draft(
        db_session, "rb-org-done", {"campaign_id": "120", "ad_id": "999"},
    )
    mock_call = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )

    await _rollback_campaign(db_session, draft.id)

    mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_records_error_without_raising(db_session: AsyncSession, mocker):
    """Si el borrado falla, se anota en el draft y NO se re-lanza: el error que
    importa es el que tumbó la Saga, no el de la compensación."""
    acc, draft = await _make_draft(db_session, "rb-org-err", {"campaign_id": "120"})
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"error": {"message": "Permissions error", "code": 200}},
    )

    await _rollback_campaign(db_session, draft.id)  # no debe lanzar

    await db_session.refresh(draft)
    assert "Permissions error" in draft.payload["result"]["rollback_error"]
