"""Aislamiento por CUENTA dentro de una organización.

El leak que motivó esto: la organización era la única unidad de aislamiento, así
que al invitar al usuario de un cliente a una organización que aloja las cuentas
de varios clientes, ese usuario veía todas — en Configuración y en el selector
global. Ser miembro era todo o nada.

`user_ad_accounts` es la concesión explícita y es **fail-closed**: sin filas,
ninguna cuenta.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.models.user_ad_account import UserAdAccount


async def _org_con_dos_cuentas(db: AsyncSession, slug: str):
    org = Organization(slug=slug, name="Agencia", status="ACTIVE")
    db.add(org)
    await db.flush()
    cuentas = []
    for nombre in ("ITS Institute", "Otro Cliente"):
        acc = AdAccount(
            organization_id=org.id, platform="META",
            external_account_id=f"act_{slug}_{len(cuentas)}",
            name=nombre, status="ACTIVE",
        )
        db.add(acc)
        cuentas.append(acc)
    # Commit y no flush: el signup de `_miembro` va por la API con OTRA sesión, y
    # con SQLite una transacción de escritura abierta la bloquea.
    await db.commit()
    return org, cuentas[0], cuentas[1]


async def _miembro(async_client: AsyncClient, db: AsyncSession, org, email: str, concede):
    """Usuario nuevo, miembro de `org`, con `concede` como únicas cuentas."""
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123", "full_name": "Miembro",
        "organization_slug": f"{org.slug}-propia-{email.split('@')[0]}",
        "organization_name": "Org propia",
    })
    token = res.json()["access_token"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    for acc in concede:
        db.add(UserAdAccount(
            user_id=user.id, ad_account_id=acc.id, organization_id=org.id
        ))
    await db.commit()
    return user, {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug}


@pytest.mark.asyncio
async def test_solo_se_listan_las_cuentas_concedidas(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El síntoma exacto: en Configuración aparecían todas las cuentas de la
    organización, no solo la del cliente que las mira."""
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-lista")
    _, headers = await _miembro(async_client, db_session, org, "its@example.com", [its])

    res = await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=headers
    )
    assert res.status_code == 200
    nombres = [a["name"] for a in res.json()]
    assert nombres == ["ITS Institute"]


@pytest.mark.asyncio
async def test_las_archivadas_ajenas_tampoco_aparecen(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Configuración pide `include_archived=true`: el filtro por concesión tiene
    que valer también ahí, o archivar una cuenta la volvería visible."""
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-archivadas")
    otro.status = "ARCHIVED"
    await db_session.commit()
    _, headers = await _miembro(async_client, db_session, org, "its2@example.com", [its])

    res = await async_client.get(
        "/api/v1/organizations/my/ad-accounts?include_archived=true", headers=headers
    )
    assert [a["name"] for a in res.json()] == ["ITS Institute"]


@pytest.mark.asyncio
async def test_el_header_de_una_cuenta_no_concedida_da_403(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Listar de menos no sirve de nada si el id se puede mandar a mano: el
    aislamiento real está en `get_active_ad_account_id`, por donde pasa todo."""
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-header")
    _, headers = await _miembro(async_client, db_session, org, "its3@example.com", [its])

    ok = await async_client.get(
        "/api/v1/campaigns/recent", headers={**headers, "X-Ad-Account-Id": its.id}
    )
    assert ok.status_code == 200

    leak = await async_client.get(
        "/api/v1/campaigns/recent", headers={**headers, "X-Ad-Account-Id": otro.id}
    )
    assert leak.status_code == 403


@pytest.mark.asyncio
async def test_con_una_sola_concedida_no_pide_elegir(
    async_client: AsyncClient, db_session: AsyncSession
):
    """La organización tiene dos cuentas, pero este usuario ve una: no
    corresponde el 400 de "elige una cuenta", corresponde auto-seleccionarla."""
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-autoselect")
    _, headers = await _miembro(async_client, db_session, org, "its4@example.com", [its])

    res = await async_client.get("/api/v1/campaigns/recent", headers=headers)
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_sin_ninguna_concedida_lo_dice_en_vez_de_mostrar_todo(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Devolver "sin cuenta activa" sería el leak: una docena de endpoints
    interpretan eso como "toda la organización"."""
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-sin-nada")
    _, headers = await _miembro(async_client, db_session, org, "its5@example.com", [])

    res = await async_client.get("/api/v1/campaigns/recent", headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "NO_AD_ACCOUNTS_GRANTED"

    # Configuración sigue respondiendo, para que un ADMIN pueda arreglarlo.
    lista = await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=headers
    )
    assert lista.status_code == 200 and lista.json() == []


@pytest.mark.asyncio
async def test_archivar_una_cuenta_ajena_da_404(
    async_client: AsyncClient, db_session: AsyncSession
):
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-archivar")
    _, headers = await _miembro(async_client, db_session, org, "its6@example.com", [its])

    res = await async_client.post(
        f"/api/v1/organizations/my/ad-accounts/{otro.id}/archive", headers=headers
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_un_admin_cambia_las_cuentas_de_un_miembro(
    async_client: AsyncClient, db_session: AsyncSession
):
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-admin")
    admin, admin_headers = await _miembro(
        async_client, db_session, org, "admin@example.com", [its, otro]
    )
    cliente, cliente_headers = await _miembro(
        async_client, db_session, org, "cliente@example.com", [its, otro]
    )

    ver = await async_client.get(
        f"/api/v1/organizations/my/members/{cliente.id}/ad-accounts", headers=admin_headers
    )
    assert ver.status_code == 200
    assert set(ver.json()["granted_ids"]) == {its.id, otro.id}
    assert len(ver.json()["available"]) == 2

    # Al cliente se le deja solo la suya.
    res = await async_client.put(
        f"/api/v1/organizations/my/members/{cliente.id}/ad-accounts",
        json={"ad_account_ids": [its.id]}, headers=admin_headers,
    )
    assert res.status_code == 200

    lista = await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=cliente_headers
    )
    assert [a["name"] for a in lista.json()] == ["ITS Institute"]


@pytest.mark.asyncio
async def test_un_admin_no_puede_dejarse_sin_ninguna_cuenta(
    async_client: AsyncClient, db_session: AsyncSession
):
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-autolock")
    admin, headers = await _miembro(
        async_client, db_session, org, "admin2@example.com", [its, otro]
    )

    res = await async_client.put(
        f"/api/v1/organizations/my/members/{admin.id}/ad-accounts",
        json={"ad_account_ids": []}, headers=headers,
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_no_se_pueden_tocar_las_concesiones_de_otra_organizacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    org_a, its_a, otro_a = await _org_con_dos_cuentas(db_session, "grants-org-a")
    org_b, its_b, otro_b = await _org_con_dos_cuentas(db_session, "grants-org-b")
    _, headers_a = await _miembro(async_client, db_session, org_a, "a@example.com", [its_a])
    ajeno, _ = await _miembro(async_client, db_session, org_b, "b@example.com", [its_b])

    ver = await async_client.get(
        f"/api/v1/organizations/my/members/{ajeno.id}/ad-accounts", headers=headers_a
    )
    assert ver.status_code == 404

    escribir = await async_client.put(
        f"/api/v1/organizations/my/members/{ajeno.id}/ad-accounts",
        json={"ad_account_ids": [its_a.id]}, headers=headers_a,
    )
    assert escribir.status_code == 404


@pytest.mark.asyncio
async def test_conectar_una_cuenta_la_concede_a_quien_la_conecta(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """El modelo es fail-closed: sin la concesión, la cuenta recién conectada
    desaparecería de la pantalla de quien acaba de conectarla."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "conecta@example.com", "password": "securepassword123",
        "full_name": "Conector", "organization_slug": "grants-conectar",
        "organization_name": "Conectar Org",
    })
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    creada = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={"platform": "META", "external_account_id": "998877", "name": "Nueva",
              "access_token": "mock-token"},
        headers=headers,
    )
    assert creada.status_code == 200

    lista = await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=headers
    )
    assert [a["external_account_id"] for a in lista.json()] == ["act_998877"]


@pytest.mark.asyncio
async def test_la_invitacion_decide_a_que_cuentas_entra_el_invitado(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Sin esto, un invitado nuevo entra y no ve nada hasta que un ADMIN se
    acuerde de darle acceso."""
    org, its, otro = await _org_con_dos_cuentas(db_session, "grants-invita")
    _, admin_headers = await _miembro(
        async_client, db_session, org, "invitador@example.com", [its, otro]
    )

    inv = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "invitado@example.com", "role": "OPERATOR",
              "ad_account_ids": [its.id]},
        headers=admin_headers,
    )
    assert inv.status_code == 201
    token = inv.json()["token"]

    aceptada = await async_client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"full_name": "Invitado", "password": "securepassword123"},
    )
    assert aceptada.status_code == 200
    invitado_headers = {
        "Authorization": f"Bearer {aceptada.json()['access_token']}",
        "X-Tenant-Slug": org.slug,
    }

    lista = await async_client.get(
        "/api/v1/organizations/my/ad-accounts", headers=invitado_headers
    )
    assert [a["name"] for a in lista.json()] == ["ITS Institute"]


@pytest.mark.asyncio
async def test_una_invitacion_no_puede_conceder_cuentas_de_otra_organizacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    org_a, its_a, _otro_a = await _org_con_dos_cuentas(db_session, "grants-inv-a")
    org_b, its_b, _otro_b = await _org_con_dos_cuentas(db_session, "grants-inv-b")
    _, headers_a = await _miembro(
        async_client, db_session, org_a, "inva@example.com", [its_a]
    )

    res = await async_client.post(
        "/api/v1/organizations/my/invitations",
        json={"email": "colado@example.com", "role": "VIEWER",
              "ad_account_ids": [its_b.id]},
        headers=headers_a,
    )
    assert res.status_code == 400
