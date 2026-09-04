"""Panel de administración de plataforma (execution/02).

El test que más importa acá es `test_el_admin_de_plataforma_no_ve_los_datos_de_un_cliente`.
El aislamiento por organización es una de las reglas innegociables del
proyecto, y un rol nuevo que la saltee en silencio la deroga de hecho.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitation import Invitation, hash_invitation_token
from app.models.organization import Organization
from app.models.user import User
from app.tenant_rules.modules import AVAILABLE_MODULES, enabled_modules_for


@pytest.fixture(autouse=True)
def _no_emails(mocker):
    """El alta manda un correo real; en tests se intercepta."""
    return mocker.patch("app.api.v1.admin.send_email")


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"user@{slug}.example.com", "password": "securepassword123",
        "full_name": "Admin Tester", "organization_slug": slug, "organization_name": "Admin Org",
    })
    body = res.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["organization_id"], body["user"]["id"]


async def _hacer_platform_admin(db: AsyncSession, user_id: str):
    await db.execute(update(User).where(User.id == user_id).values(is_platform_admin=True))
    await db.commit()


# --- El limite de privilegio ---

@pytest.mark.asyncio
async def test_un_admin_de_organizacion_no_entra_al_panel(async_client: AsyncClient):
    """ADMIN es un rol DENTRO de una organización. Un ADMIN del Cliente A no
    puede crear el Cliente B ni tocar sus módulos."""
    headers, _, _ = await _signup(async_client, "adm-cliente")
    assert (await async_client.get("/api/v1/admin/organizations", headers=headers)).status_code == 403
    assert (await async_client.get("/api/v1/admin/modules", headers=headers)).status_code == 403
    creacion = await async_client.post(
        "/api/v1/admin/organizations", headers=headers,
        json={"slug": "robado", "name": "Robado"},
    )
    assert creacion.status_code == 403


@pytest.mark.asyncio
async def test_el_admin_de_plataforma_no_ve_los_datos_de_un_cliente(
    async_client: AsyncClient, db_session: AsyncSession
):
    """LA regla del archivo 02: el flag administra organizaciones, NO abre sus
    datos. Un admin de plataforma sigue viendo únicamente su propia
    organización cuando pide campañas, reportes o KPIs.

    Si este test se rompe, el aislamiento por organización dejó de ser cierto.
    """
    headers_a, org_a, user_a = await _signup(async_client, "adm-plataforma")
    headers_b, org_b, _ = await _signup(async_client, "adm-otro-cliente")
    await _hacer_platform_admin(db_session, user_a)

    # Puede administrar: ve la lista de organizaciones.
    lista = await async_client.get("/api/v1/admin/organizations", headers=headers_a)
    assert lista.status_code == 200
    slugs = {o["slug"] for o in lista.json()}
    assert {"adm-plataforma", "adm-otro-cliente"} <= slugs

    # Pero la lista trae CONTEOS, no contenido del tenant.
    fila = next(o for o in lista.json() if o["slug"] == "adm-otro-cliente")
    assert set(fila) == {
        "id", "slug", "name", "display_name", "status", "industry", "accent_color",
        "enabled_modules", "miembros", "cuentas_publicitarias", "created_at",
        "invitacion",
    }
    # El enlace de invitacion solo vuelve al CREARLA. Listar organizaciones no
    # puede devolver tokens de acceso a los espacios de otros clientes.
    assert fila["invitacion"] is None

    # Y los endpoints de datos siguen acotados a SU organización: pedir la
    # cuenta de otro tenant sigue dando 403, flag o no flag.
    otras_cuentas = await async_client.get(
        "/api/v1/campaigns/recent",
        headers={**headers_a, "X-Ad-Account-Id": "cuenta-del-cliente-b"},
    )
    assert otras_cuentas.status_code == 403


# --- Alta de un cliente ---

@pytest.mark.asyncio
async def test_alta_de_cliente_crea_la_org_y_la_invitacion_espera_al_alta_completa(
    async_client: AsyncClient, db_session: AsyncSession, _no_emails
):
    """Nadie escribe la contraseña del cliente: se reusa la invitación de un
    solo uso que ya existía.

    Desde la spec 05 el alta es una SECUENCIA: crear la organización e invitar al
    cliente en el mismo paso es lo que permitía que entrara a un espacio sin
    Meta, sin IA y sin WhatsApp. El email queda como intención registrada y la
    invitación sale cuando el alta está completa.
    """
    headers, _, user_id = await _signup(async_client, "adm-alta")
    await _hacer_platform_admin(db_session, user_id)

    res = await async_client.post("/api/v1/admin/organizations", headers=headers, json={
        "slug": "cliente-nuevo", "name": "Cliente Nuevo",
        "display_name": "Cliente Nuevo SAC", "accent_color": "#02b8cc",
        "enabled_modules": ["campanas", "chat"],
        "admin_email": "dueno@clientenuevo.com",
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["slug"] == "cliente-nuevo"
    assert set(body["enabled_modules"]) == {"campanas", "chat", "settings"}

    # Todavía no hay invitación: falta validar el alta.
    assert body["invitacion"] is None
    assert (await db_session.execute(
        select(Invitation).where(Invitation.email == "dueno@clientenuevo.com")
    )).scalar_one_or_none() is None
    org = await db_session.get(Organization, body["id"])
    assert org.onboarding_state["admin_email_previsto"] == "dueno@clientenuevo.com"

    # Con el alta resuelta, la invitación sale y nadie escribió una contraseña.
    for paso in ("meta", "ia", "whatsapp"):
        r = await async_client.post(
            f"/api/v1/admin/organizations/{org.id}/onboarding/{paso}",
            headers=headers, json={"omitir": True, "motivo": "alta de prueba"},
        )
        assert r.status_code == 200, r.text

    invitado = await async_client.post(
        f"/api/v1/admin/organizations/{org.id}/invite-admin", headers=headers, json={},
    )
    assert invitado.status_code == 201, invitado.text

    inv = (await db_session.execute(
        select(Invitation).where(Invitation.email == "dueno@clientenuevo.com")
    )).scalar_one()
    assert inv.role == "ADMIN"
    # Lo persistido es el hash: el token en claro solo existe en la respuesta
    # que crea la invitación (spec 01).
    assert inv.token is None
    assert inv.token_hash and len(inv.token_hash) == 64
    _no_emails.assert_awaited_once()

    token_en_claro = invitado.json()["invite_url"].rsplit("/", 1)[-1]
    assert inv.token_hash == hash_invitation_token(token_en_claro)
    assert invitado.json()["email"] == "dueno@clientenuevo.com"
    # Y distingue "se mando" de "se creo y nadie lo mando".
    assert invitado.json()["correo_enviado"] is False   # sin BREVO_API_KEY en tests


@pytest.mark.asyncio
async def test_alta_sin_email_no_crea_invitacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Se puede dar de alta la organizacion y invitar despues."""
    headers, _, user_id = await _signup(async_client, "adm-sin-invitado")
    await _hacer_platform_admin(db_session, user_id)

    res = await async_client.post("/api/v1/admin/organizations", headers=headers,
                                  json={"slug": "sin-invitado", "name": "Sin Invitado"})
    assert res.status_code == 201
    assert res.json()["invitacion"] is None


@pytest.mark.asyncio
async def test_no_deja_pisar_un_slug_existente_ni_uno_reservado(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, _, user_id = await _signup(async_client, "adm-slugs")
    await _hacer_platform_admin(db_session, user_id)

    repetido = await async_client.post("/api/v1/admin/organizations", headers=headers,
                                       json={"slug": "adm-slugs", "name": "Otro"})
    assert repetido.status_code == 400

    for reservado in ("www", "api", "admin"):
        res = await async_client.post("/api/v1/admin/organizations", headers=headers,
                                      json={"slug": reservado, "name": "Reservado"})
        assert res.status_code == 400, reservado


@pytest.mark.asyncio
async def test_una_org_creada_sin_modulos_los_tiene_todos(
    async_client: AsyncClient, db_session: AsyncSession
):
    """`None` = todos habilitados: mismo contrato que el resto del sistema."""
    headers, _, user_id = await _signup(async_client, "adm-sin-modulos")
    await _hacer_platform_admin(db_session, user_id)

    res = await async_client.post("/api/v1/admin/organizations", headers=headers,
                                  json={"slug": "todo-incluido", "name": "Todo"})
    assert res.status_code == 201
    assert set(res.json()["enabled_modules"]) == set(AVAILABLE_MODULES)

    org = (await db_session.execute(
        select(Organization).where(Organization.slug == "todo-incluido")
    )).scalar_one()
    assert org.enabled_modules is None


# --- Modulos ---

@pytest.mark.asyncio
async def test_prender_y_apagar_modulos_de_un_cliente(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, _, user_id = await _signup(async_client, "adm-toggle")
    headers_cli, org_cliente, _ = await _signup(async_client, "adm-toggle-cli")
    await _hacer_platform_admin(db_session, user_id)

    res = await async_client.patch(
        f"/api/v1/admin/organizations/{org_cliente}/modules",
        headers=headers, json={"enabled_modules": ["campanas", "chat"]},
    )
    assert res.status_code == 200
    assert set(res.json()["enabled_modules"]) == {"campanas", "chat", "settings"}

    # Y el efecto es real del lado del CLIENTE: la API le cierra el modulo.
    # Desde la spec 05 hay que marcar la membresia como tal — el modulo acota la
    # experiencia del cliente, no la de la agencia.
    from sqlalchemy import update as _update

    from app.models.organization_user import OrganizationUser

    await db_session.execute(
        _update(OrganizationUser)
        .where(OrganizationUser.organization_id == org_cliente)
        .values(access_kind="CLIENT")
    )
    await db_session.commit()

    apagado = await async_client.get("/api/v1/ai-usage", headers=headers_cli)
    assert apagado.status_code == 403


@pytest.mark.asyncio
async def test_un_modulo_inventado_se_rechaza(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, user_id = await _signup(async_client, "adm-modulo-falso")
    await _hacer_platform_admin(db_session, user_id)

    res = await async_client.patch(
        f"/api/v1/admin/organizations/{org_id}/modules",
        headers=headers, json={"enabled_modules": ["campanas", "modulo-inventado"]},
    )
    assert res.status_code == 400
    assert "modulo-inventado" in res.json()["detail"]


@pytest.mark.asyncio
async def test_el_slug_no_se_puede_cambiar(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Es el subdominio del tenant: renombrarlo dejaria fuera a sus usuarios."""
    headers, org_id, user_id = await _signup(async_client, "adm-slug-fijo")
    await _hacer_platform_admin(db_session, user_id)

    await async_client.patch(f"/api/v1/admin/organizations/{org_id}", headers=headers,
                             json={"slug": "otro-slug", "name": "Renombrada"})
    org = await db_session.get(Organization, org_id)
    await db_session.refresh(org)
    assert org.slug == "adm-slug-fijo"
    assert org.name == "Renombrada"
