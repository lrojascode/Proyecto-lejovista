"""Tests de CampaignValidator: reproduce el bug encontrado con datos reales
(cuenta "Agencia Kibo") donde una organización con 2+ AdAccount conectados
hacía que la validación de creación de campañas fallara con
`MultipleResultsFound`, porque el stack viejo (MetaCredentialService)
resolvía el token por organización, no por cuenta específica."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.schemas.campaign_schemas import CampaignCreateRequest
from app.services.campaign_validator import CampaignValidator


async def _make_org_with_two_accounts(db: AsyncSession, slug="validator-org"):
    org = Organization(slug=slug, name="Validator Org", status="ACTIVE")
    db.add(org)
    await db.flush()

    acc_a = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1111111111", name="Cuenta A", status="ACTIVE",
    )
    acc_a.set_access_token("token-a")
    acc_b = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_2222222222", name="Cuenta B", status="ACTIVE",
    )
    acc_b.set_access_token("token-b")
    db.add(acc_a)
    db.add(acc_b)
    await db.flush()
    return org, acc_a, acc_b


def _request(ad_account_id: str):
    return CampaignCreateRequest(
        ad_account_id=ad_account_id, page_id="page-1",
        product_name="CableGPT", budget=100.0, days=7,
        whatsapp_number="51999888777", image_hash="hash1", copy_text="Copy",
    )


@pytest.mark.asyncio
async def test_validate_succeeds_with_two_ad_accounts_in_same_org(db_session: AsyncSession, mocker, monkeypatch):
    """Reproduce exactamente el bug real: la org tiene 2 AdAccount (2 credenciales
    ACTIVE). Antes, el validador resolvía el token por organization_id con
    scalar_one_or_none() -> MultipleResultsFound. Ahora usa el AdAccount ya
    resuelto — no hay ambigüedad posible."""
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    org, acc_a, acc_b = await _make_org_with_two_accounts(db_session)
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True,
        return_value={"account_status": 1, "spend_cap": 0, "amount_spent": 0},
    )

    # No debe lanzar excepción alguna, ni MultipleResultsFound ni HTTPException.
    await CampaignValidator(acc_a).validate(_request(acc_a.id))
    await CampaignValidator(acc_b).validate(_request(acc_b.id))


@pytest.mark.asyncio
async def test_validate_uses_correct_ad_account_external_id(db_session: AsyncSession, mocker, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    org, acc_a, acc_b = await _make_org_with_two_accounts(db_session)
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True,
        return_value={"account_status": 1, "spend_cap": 0, "amount_spent": 0},
    )

    await CampaignValidator(acc_b).validate(_request(acc_b.id))

    _, endpoint, _params = mock_call_api.call_args[0]
    assert endpoint == "act_2222222222"  # no "act_<uuid interno>"


@pytest.mark.asyncio
async def test_validate_rejects_suspended_account(db_session: AsyncSession, mocker, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    org, acc_a, _ = await _make_org_with_two_accounts(db_session, slug="validator-org-suspended")
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True,
        return_value={"account_status": 2, "spend_cap": 0, "amount_spent": 0},
    )

    with pytest.raises(Exception) as exc_info:
        await CampaignValidator(acc_a).validate(_request(acc_a.id))
    assert "status" in str(exc_info.value).lower() or "2" in str(exc_info.value)


@pytest.mark.asyncio
async def test_validate_rejects_spend_cap_reached(db_session: AsyncSession, mocker, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    org, acc_a, _ = await _make_org_with_two_accounts(db_session, slug="validator-org-cap")
    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True,
        return_value={"account_status": 1, "spend_cap": 100, "amount_spent": 100},
    )

    with pytest.raises(Exception):
        await CampaignValidator(acc_a).validate(_request(acc_a.id))


@pytest.mark.asyncio
async def test_validate_skips_real_call_when_mock_services_enabled(db_session: AsyncSession, mocker, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    org, acc_a, _ = await _make_org_with_two_accounts(db_session, slug="validator-org-mock")
    mock_call_api = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api", autospec=True
    )

    await CampaignValidator(acc_a).validate(_request(acc_a.id))

    mock_call_api.assert_not_called()
