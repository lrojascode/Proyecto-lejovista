"""Tests de S-1 (RBAC): OrganizationUser.role se guardaba pero ningún
endpoint lo verificaba — un VIEWER tenía en la práctica permisos de ADMIN.
Cubre la jerarquía de require_role (VIEWER < OPERATOR < ADMIN) contra
endpoints representativos de cada nivel, y el cierre de la escalada de
privilegios relacionada: antes, registrarse contra el slug de una
organización YA EXISTENTE vinculaba al nuevo usuario como ADMIN de esa
organización sin invitación ni verificación alguna."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization_user import OrganizationUser


async def _signup(async_client: AsyncClient, slug: str, email_prefix: str = "admin"):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{email_prefix}@{slug}.example.com", "password": "securepassword123",
        "full_name": "RBAC Tester", "organization_slug": slug, "organization_name": "RBAC Org",
    })
    body = res.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    return headers, body["user"]["organization_id"]


async def _set_role(db_session: AsyncSession, org_id: str, role: str):
    await db_session.execute(
        update(OrganizationUser)
        .where(OrganizationUser.organization_id == org_id)
        .values(role=role)
    )
    await db_session.commit()


@pytest.fixture(autouse=True)
def _mock_side_effects(mocker):
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    mocker.patch("app.api.v1.insights.run_ai_analysis")
    mock_chain_factory = mocker.patch("app.services.campaign_creation.chain")
    mock_chain_instance = mock_chain_factory.return_value
    mock_chain_instance.on_error.return_value = mock_chain_instance
    mocker.patch(
        "app.services.campaign_creation.CampaignValidator.validate", autospec=True, return_value=None
    )


# --- Escalada de privilegios vía signup (hallazgo relacionado a S-1) ---

@pytest.mark.asyncio
async def test_signup_rejects_joining_existing_org_slug(async_client: AsyncClient):
    """Antes: registrarse con el slug de una org existente vinculaba al nuevo
    usuario como ADMIN de esa org sin invitación — toma de cuenta sin auth."""
    await _signup(async_client, "taken-slug", email_prefix="founder")

    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "intruder@taken-slug.example.com", "password": "securepassword123",
        "full_name": "Intruder", "organization_slug": "taken-slug", "organization_name": "Whatever",
    })
    assert res.status_code == 400


# --- Jerarquía de roles ---

@pytest.mark.asyncio
async def test_viewer_blocked_from_operator_endpoint(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "rbac-viewer-op")
    await _set_role(db_session, org_id, "VIEWER")

    res = await async_client.post("/api/v1/campaigns/sync-trigger", headers=headers)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_operator_allowed_on_operator_endpoint(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "rbac-operator-op")
    await _set_role(db_session, org_id, "OPERATOR")

    res = await async_client.post("/api/v1/insights/analyze-trigger", headers=headers)
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_operator_blocked_from_admin_endpoint(async_client: AsyncClient, db_session: AsyncSession):
    """La jerarquía corta en ambos sentidos: OPERATOR no basta para una
    operación reservada a ADMIN (archivar/conectar cuentas, cambiar la org)."""
    headers, org_id = await _signup(async_client, "rbac-operator-admin")
    await _set_role(db_session, org_id, "OPERATOR")

    res = await async_client.put(
        "/api/v1/organizations/my", json={"name": "Nuevo nombre"}, headers=headers
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_allowed_everywhere(async_client: AsyncClient, db_session: AsyncSession):
    """ADMIN (rol por defecto del fundador de la org, sin downgrade) sigue
    pudiendo operar — no regresión funcional para el caso normal."""
    headers, org_id = await _signup(async_client, "rbac-admin-ok")

    res = await async_client.put(
        "/api/v1/organizations/my", json={"name": "Nuevo nombre"}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Nuevo nombre"


@pytest.mark.asyncio
async def test_viewer_blocked_from_create_campaign(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "rbac-viewer-campaign")
    acc_res = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={"platform": "META", "external_account_id": "act_1", "name": "Acc", "access_token": "tok"},
        headers=headers,
    )
    ad_account_id = acc_res.json()["id"]
    await _set_role(db_session, org_id, "VIEWER")

    res = await async_client.post("/api/v1/campaigns/", json={
        "ad_account_id": ad_account_id, "page_id": "page-1", "product_name": "CableGPT",
        "budget": 200.0, "days": 5, "whatsapp_number": "51999888777",
        "image_hash": "existing_hash", "copy_text": "Hook + beneficios + CTA",
    }, headers=headers)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_role_missing_from_org_denied(async_client: AsyncClient, db_session: AsyncSession):
    """Si por alguna razón no hay fila OrganizationUser para la org activa
    (dato corrupto/borrado), require_role deniega en vez de asumir acceso."""
    headers, org_id = await _signup(async_client, "rbac-no-membership")
    await db_session.execute(
        OrganizationUser.__table__.delete().where(OrganizationUser.organization_id == org_id)
    )
    await db_session.commit()

    res = await async_client.post("/api/v1/campaigns/sync-trigger", headers=headers)
    assert res.status_code == 403
