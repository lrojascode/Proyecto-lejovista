"""Tests de la Fase 2 del plan de creación de campañas: anti-bloqueo (tope de
campañas por sesión) y nivel de riesgo expuesto en POST /campaigns/."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from app.services.campaign_creation import MAX_CAMPAIGNS_PER_SESSION


async def _signup_with_ad_account(async_client: AsyncClient, slug: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@example.com", "password": "securepassword123",
        "full_name": "Creator Tester", "organization_slug": slug, "organization_name": "Creator Org",
    })
    body = signup_res.json()
    token = body["access_token"]
    org_id = body["user"]["organization_id"]
    headers = {"Authorization": f"Bearer {token}"}

    acc_res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={
            "platform": "META", "external_account_id": "act_1",
            "name": "Creator Acc", "access_token": "mock-token",
        },
        headers=headers,
    )
    ad_account_id = acc_res.json()["id"]
    return headers, org_id, ad_account_id


def _campaign_payload(ad_account_id: str):
    return {
        "ad_account_id": ad_account_id,
        "page_id": "page-1",
        "product_name": "CableGPT",
        "budget": 200.0,
        "days": 5,
        "whatsapp_number": "51999888777",
        "image_hash": "existing_hash",
        "copy_text": "Hook + beneficios + CTA",
    }


@pytest.fixture(autouse=True)
def _mock_validator_and_chain(mocker):
    # La validación síncrona hace un GET real a Meta (MetaApiClient no respeta
    # MOCK_SERVICES, ver A-3 en el HANDOFF) — se mockea para no requerir red.
    mocker.patch(
        "app.services.campaign_creation.CampaignValidator.validate", autospec=True, return_value=None
    )
    # Conectar una cuenta dispara un sync_ad_account.delay(...) real (ver
    # mock_celery en test_api_v1.py) — sin esto, .delay() intenta hablar con un
    # broker/backend de Redis real que no existe en el entorno de test.
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    # El chain de Celery no debe intentar conectarse a un broker real en tests.
    mock_chain_factory = mocker.patch("app.services.campaign_creation.chain")
    mock_chain_instance = mock_chain_factory.return_value
    mock_chain_instance.on_error.return_value = mock_chain_instance
    return mock_chain_instance


@pytest.mark.asyncio
async def test_create_campaign_returns_medium_risk_level(async_client: AsyncClient):
    headers, org_id, ad_account_id = await _signup_with_ad_account(async_client, "risk-org")

    res = await async_client.post(
        "/api/v1/campaigns/", json=_campaign_payload(ad_account_id), headers=headers
    )
    assert res.status_code == 202
    assert res.json()["risk_level"] == "MEDIUM"


@pytest.mark.asyncio
async def test_create_campaign_blocks_after_session_cap(async_client: AsyncClient, db_session: AsyncSession):
    """Playbook §2: máx ~5 campañas nuevas por sesión (anti-bloqueo de cuenta)."""
    headers, org_id, ad_account_id = await _signup_with_ad_account(async_client, "cap-org")

    for _ in range(MAX_CAMPAIGNS_PER_SESSION):
        res = await async_client.post(
            "/api/v1/campaigns/", json=_campaign_payload(ad_account_id), headers=headers
        )
        assert res.status_code == 202

    blocked_res = await async_client.post(
        "/api/v1/campaigns/", json=_campaign_payload(ad_account_id), headers=headers
    )
    assert blocked_res.status_code == 429
    assert "5 campañas" in blocked_res.json()["detail"]


@pytest.mark.asyncio
async def test_create_campaign_session_cap_scoped_per_organization(async_client: AsyncClient):
    """Los drafts de otra organización no deben contar contra el tope propio."""
    headers_a, _, ad_account_a = await _signup_with_ad_account(async_client, "cap-org-a")
    headers_b, _, ad_account_b = await _signup_with_ad_account(async_client, "cap-org-b")

    for _ in range(MAX_CAMPAIGNS_PER_SESSION):
        res = await async_client.post(
            "/api/v1/campaigns/", json=_campaign_payload(ad_account_a), headers=headers_a
        )
        assert res.status_code == 202

    # Org B no ha creado ninguna todavía -> no debe estar bloqueada.
    res_b = await async_client.post(
        "/api/v1/campaigns/", json=_campaign_payload(ad_account_b), headers=headers_b
    )
    assert res_b.status_code == 202
