"""Tests para el flujo de invitaciones de usuarios y control de roles (Fase 6.1, Roadmap 4)."""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User


async def _signup_org_and_admin(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.com",
        "password": "securepassword123",
        "full_name": "Admin User",
        "organization_slug": slug,
        "organization_name": f"Org {slug}",
    })
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": slug}


async def _signup_viewer_user(async_client: AsyncClient, db: AsyncSession, org: Organization, email: str):
    signup_res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123",
        "full_name": "Viewer User", "organization_slug": f"{org.slug}-viewer-{email.split('@')[0]}",
        "organization_name": "Personal Org",
    })
    token = signup_res.json()["access_token"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="VIEWER"))
    await db.commit()
    return {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug}


@pytest.mark.asyncio
async def test_only_admin_can_send_invitations(async_client: AsyncClient, db_session: AsyncSession):
    headers_admin = await _signup_org_and_admin(async_client, "invite-role-test")
    q_org = select(Organization).where(Organization.slug == "invite-role-test")
    org = (await db_session.execute(q_org)).scalar_one()
    headers_viewer = await _signup_viewer_user(async_client, db_session, org, "viewer_test@example.com")

    # VIEWER intentando invitar -> 403
    res_v = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "newbie@example.com", "role": "OPERATOR"},
        headers=headers_viewer,
    )
    assert res_v.status_code == 403

    # ADMIN invitando -> 201
    res_a = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "newbie@example.com", "role": "OPERATOR"},
        headers=headers_admin,
    )
    assert res_a.status_code == 201
    data = res_a.json()
    assert data["email"] == "newbie@example.com"
    assert data["role"] == "OPERATOR"
    assert "token" in data
    # El enlace va al DOMINIO BASE, no al subdominio del cliente:
    # `accept_invitation` busca la invitacion solo por token, asi que meterle el
    # subdominio obligaba a tener DNS y certificado para ese cliente antes de
    # que alguien pudiera abrir el enlace.
    assert "invite-role-test" not in data["invite_url"]
    assert data["invite_url"].endswith(f"/invitations/{data['token']}")


@pytest.mark.asyncio
async def test_full_invitation_accept_lifecycle(async_client: AsyncClient, db_session: AsyncSession):
    headers_admin = await _signup_org_and_admin(async_client, "invite-life-test")

    # 1. Crear invitación para operador
    inv_res = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "operador@empresa.com", "role": "OPERATOR"},
        headers=headers_admin,
    )
    assert inv_res.status_code == 201
    token = inv_res.json()["token"]

    # 2. Consultar detalles públicos de la invitación
    detail_res = await async_client.get(f"/api/v1/invitations/{token}")
    assert detail_res.status_code == 200
    details = detail_res.json()
    assert details["email"] == "operador@empresa.com"
    assert details["role"] == "OPERATOR"
    assert details["organization_slug"] == "invite-life-test"
    assert details["is_expired"] is False
    assert details["is_accepted"] is False

    # 3. Aceptar la invitación
    accept_res = await async_client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"full_name": "Carlos Operador", "password": "nuevapassword123"},
    )
    assert accept_res.status_code == 200
    auth_data = accept_res.json()
    assert "access_token" in auth_data
    assert auth_data["user"]["role"] == "OPERATOR"
    assert auth_data["user"]["email"] == "operador@empresa.com"

    # 4. Intentar aceptar de nuevo -> 400
    dup_res = await async_client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"full_name": "Carlos Operador", "password": "nuevapassword123"},
    )
    assert dup_res.status_code == 400
    assert "ya fue aceptada" in dup_res.json()["detail"]


@pytest.mark.asyncio
async def test_invitation_expiration_is_enforced(async_client: AsyncClient, db_session: AsyncSession):
    headers_admin = await _signup_org_and_admin(async_client, "invite-exp-test")

    inv_res = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "expirado@empresa.com", "role": "VIEWER"},
        headers=headers_admin,
    )
    token = inv_res.json()["token"]

    # Forzar fecha de expiración al pasado en DB
    q = select(Invitation).where(Invitation.token == token)
    inv = (await db_session.execute(q)).scalar_one()
    inv.expires_at = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    db_session.add(inv)
    await db_session.commit()

    # Consultar detalles muestra is_expired = True
    detail_res = await async_client.get(f"/api/v1/invitations/{token}")
    assert detail_res.status_code == 200
    assert detail_res.json()["is_expired"] is True

    # Intentar aceptar falla con 400
    accept_res = await async_client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"full_name": "Expirado User", "password": "nuevapassword123"},
    )
    assert accept_res.status_code == 400
    assert "expirado" in accept_res.json()["detail"]


@pytest.mark.asyncio
async def test_list_and_delete_invitations_and_members(async_client: AsyncClient, db_session: AsyncSession):
    headers_admin = await _signup_org_and_admin(async_client, "invite-mgmt-test")

    # Listar miembros iniciales (solo admin)
    members_res = await async_client.get("/api/v1/organizations/my/members", headers=headers_admin)
    assert members_res.status_code == 200
    assert len(members_res.json()) == 1
    assert members_res.json()[0]["role"] == "ADMIN"

    # Crear invitación
    inv_res = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "borrar@empresa.com", "role": "VIEWER"},
        headers=headers_admin,
    )
    inv_id = inv_res.json()["id"]

    # Listar invitaciones
    list_res = await async_client.get("/api/v1/organizations/my/invitations", headers=headers_admin)
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1
    assert list_res.json()[0]["email"] == "borrar@empresa.com"

    # Revocar invitación
    del_res = await async_client.delete(f"/api/v1/organizations/my/invitations/{inv_id}", headers=headers_admin)
    assert del_res.status_code == 200

    # Comprobar que ya no aparece
    list_after = await async_client.get("/api/v1/organizations/my/invitations", headers=headers_admin)
    assert len(list_after.json()) == 0
