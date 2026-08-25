"""Alcance de páginas y números de WhatsApp por cuenta publicitaria.

`MetaPage` y `WhatsAppNumber` son por ORGANIZACIÓN. Eso alcanzaba mientras una
organización fuera un solo cliente; en una que aloja las cuentas de varios, el
dropdown de "Crear campaña" mezclaba las páginas y los números de todos. Un
número de WhatsApp ajeno en un anuncio no es un dato de más: es que los mensajes
del cliente A le lleguen al B.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.models.meta_page_ad_account import MetaPageAdAccount
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.models.user_ad_account import UserAdAccount
from app.models.whatsapp_number import WhatsAppNumber
from app.models.whatsapp_number_ad_account import WhatsAppNumberAdAccount


async def _dos_clientes_en_una_org(db: AsyncSession, slug: str):
    """Una agencia con dos clientes en la misma organización: cada uno con su
    cuenta, su página y su número. Es el escenario del leak."""
    org = Organization(slug=slug, name="Agencia", status="ACTIVE")
    db.add(org)
    await db.flush()

    armado = []
    for etiqueta in ("its", "otro"):
        acc = AdAccount(
            organization_id=org.id, platform="META",
            external_account_id=f"act_{slug}_{etiqueta}",
            name=f"Cuenta {etiqueta}", status="ACTIVE",
        )
        page = MetaPage(
            organization_id=org.id, external_page_id=f"page_{etiqueta}",
            name=f"Página {etiqueta}", whatsapp_number=f"519{etiqueta[0]}0000000",
            status="ACTIVE",
        )
        numero = WhatsAppNumber(
            organization_id=org.id, display_number=f"519{etiqueta[0]}1111111",
            source="MANUAL", status="ACTIVE",
        )
        db.add_all([acc, page, numero])
        await db.flush()
        db.add_all([
            MetaPageAdAccount(
                meta_page_id=page.id, ad_account_id=acc.id, organization_id=org.id
            ),
            WhatsAppNumberAdAccount(
                whatsapp_number_id=numero.id, ad_account_id=acc.id, organization_id=org.id
            ),
        ])
        armado.append((acc, page, numero))
    await db.flush()
    return org, armado


async def _usuario_de(async_client: AsyncClient, db: AsyncSession, org, email, cuentas):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": email, "password": "securepassword123", "full_name": "Cliente",
        "organization_slug": f"{org.slug}-propia-{email.split('@')[0]}",
        "organization_name": "Propia",
    })
    token = res.json()["access_token"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    for acc in cuentas:
        db.add(UserAdAccount(
            user_id=user.id, ad_account_id=acc.id, organization_id=org.id
        ))
    await db.commit()
    return user, {"Authorization": f"Bearer {token}", "X-Tenant-Slug": org.slug}


@pytest.mark.asyncio
async def test_solo_se_listan_las_paginas_de_las_cuentas_propias(
    async_client: AsyncClient, db_session: AsyncSession
):
    org, ((acc_its, page_its, _), (acc_otro, page_otro, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-pages"
    )
    await db_session.commit()
    _, headers = await _usuario_de(
        async_client, db_session, org, "its@example.com", [acc_its]
    )

    res = await async_client.get("/api/v1/organizations/my/pages", headers=headers)
    assert res.status_code == 200
    assert [p["external_page_id"] for p in res.json()] == ["page_its"]


@pytest.mark.asyncio
async def test_solo_se_listan_los_numeros_de_las_cuentas_propias(
    async_client: AsyncClient, db_session: AsyncSession
):
    org, ((acc_its, _, num_its), (acc_otro, _, num_otro)) = await _dos_clientes_en_una_org(
        db_session, "scope-numeros"
    )
    await db_session.commit()
    _, headers = await _usuario_de(
        async_client, db_session, org, "its2@example.com", [acc_its]
    )

    res = await async_client.get(
        "/api/v1/organizations/my/whatsapp-numbers", headers=headers
    )
    assert [n["display_number"] for n in res.json()] == [num_its.display_number]


@pytest.mark.asyncio
async def test_no_se_puede_tocar_el_whatsapp_de_una_pagina_ajena(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Listar de menos no sirve si el id se puede mandar a mano."""
    org, ((acc_its, _, _), (_, page_otro, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-patch"
    )
    await db_session.commit()
    _, headers = await _usuario_de(
        async_client, db_session, org, "its3@example.com", [acc_its]
    )

    res = await async_client.patch(
        f"/api/v1/organizations/my/pages/{page_otro.id}/whatsapp",
        json={"whatsapp_number": "51999888777"}, headers=headers,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_la_pagina_predeterminada_tiene_que_ser_de_esa_cuenta(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Poner como predeterminada la página de otro cliente terminaría publicando
    anuncios desde ella."""
    org, ((acc_its, page_its, _), (_, page_otro, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-default"
    )
    await db_session.commit()
    _, headers = await _usuario_de(
        async_client, db_session, org, "its4@example.com", [acc_its]
    )

    ajena = await async_client.patch(
        f"/api/v1/organizations/my/ad-accounts/{acc_its.id}/default-page",
        json={"page_id": page_otro.id}, headers=headers,
    )
    assert ajena.status_code == 404

    propia = await async_client.patch(
        f"/api/v1/organizations/my/ad-accounts/{acc_its.id}/default-page",
        json={"page_id": page_its.id}, headers=headers,
    )
    assert propia.status_code == 200


@pytest.mark.asyncio
async def test_el_destino_se_resuelve_con_la_pagina_de_la_cuenta(
    db_session: AsyncSession
):
    """La regla "exactamente 1 página activa con WhatsApp" miraba la
    organización entera: con tres clientes no resolvía nunca, y cuando resolvía
    podía elegir la del cliente equivocado."""
    from app.services.campaign_destination import resolve_destination

    org, ((acc_its, page_its, _), (acc_otro, page_otro, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-destino"
    )
    await db_session.commit()

    page, motivo = await resolve_destination(db_session, acc_its, org.id)
    assert motivo is None
    assert page.external_page_id == "page_its"

    page_b, motivo_b = await resolve_destination(db_session, acc_otro, org.id)
    assert motivo_b is None
    assert page_b.external_page_id == "page_otro"


@pytest.mark.asyncio
async def test_no_se_puede_crear_una_campana_con_la_pagina_de_otro_cliente(
    db_session: AsyncSession
):
    from fastapi import HTTPException
    from app.services.campaign_creation import _rechazar_pagina_ajena

    org, ((acc_its, page_its, _), (_, page_otro, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-crear"
    )
    await db_session.commit()

    # La propia pasa.
    await _rechazar_pagina_ajena(db_session, org.id, acc_its, page_its.external_page_id)

    # La del otro cliente, no.
    with pytest.raises(HTTPException) as exc:
        await _rechazar_pagina_ajena(db_session, org.id, acc_its, page_otro.external_page_id)
    assert exc.value.status_code == 400

    # Una página desconocida se deja pasar: el descubrimiento tiene huecos
    # conocidos y bloquearla acá los convertiría en campañas imposibles.
    await _rechazar_pagina_ajena(db_session, org.id, acc_its, "page_que_no_esta_en_bd")


@pytest.mark.asyncio
async def test_el_sync_poda_los_enlaces_que_ya_no_corresponden(db_session: AsyncSession):
    """Sin poda, el seed amplio de la migración se quedaría amplio para siempre
    — la lección de 44f4ec2, donde el arreglo no limpió lo ya guardado."""
    from app.services.meta_ads.sync import MetaAdsSyncService

    org, ((acc_its, page_its, _), (_, page_otro, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-poda"
    )
    # Estado "post-migración": la cuenta de ITS enlazada también a la página ajena.
    db_session.add(MetaPageAdAccount(
        meta_page_id=page_otro.id, ad_account_id=acc_its.id, organization_id=org.id
    ))
    await db_session.commit()

    svc = MetaAdsSyncService(db_session)
    await svc._reconciliar_alcance(
        MetaPageAdAccount, "meta_page_id", acc_its, {page_its.id}
    )
    await db_session.commit()

    quedan = (await db_session.execute(select(MetaPageAdAccount.meta_page_id).where(
        MetaPageAdAccount.ad_account_id == acc_its.id
    ))).scalars().all()
    assert quedan == [page_its.id]


@pytest.mark.asyncio
async def test_sin_resultados_no_se_poda_nada(db_session: AsyncSession):
    """Un token vencido da cero páginas igual que "esta cuenta no promociona
    ninguna". Entre dejar un enlace de más y dejar una cuenta sin páginas para
    crear campañas, se deja el de más."""
    from app.services.meta_ads.sync import MetaAdsSyncService

    org, ((acc_its, page_its, _), _) = await _dos_clientes_en_una_org(
        db_session, "scope-sin-poda"
    )
    await db_session.commit()

    svc = MetaAdsSyncService(db_session)
    await svc._reconciliar_alcance(MetaPageAdAccount, "meta_page_id", acc_its, set())
    await db_session.commit()

    quedan = (await db_session.execute(select(MetaPageAdAccount.meta_page_id).where(
        MetaPageAdAccount.ad_account_id == acc_its.id
    ))).scalars().all()
    assert quedan == [page_its.id]


@pytest.mark.asyncio
async def test_lo_cargado_sin_cuentas_lo_adopta_la_primera_cuenta(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """Una organización recién creada puede cargar su número antes de conectar
    Meta. Ese número no tiene dueño, y al conectar la primera cuenta hay que
    dárselo: si no, desaparece justo cuando el filtro por cuenta empieza a
    aplicar."""
    mocker.patch("app.api.v1.organizations.sync_ad_account")
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": "huerfano@example.com", "password": "securepassword123",
        "full_name": "Nuevo", "organization_slug": "scope-huerfano",
        "organization_name": "Nueva Org",
    })
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    alta = await async_client.post(
        "/api/v1/organizations/my/whatsapp-numbers",
        json={"display_number": "51911223344"}, headers=headers,
    )
    assert alta.status_code == 201

    # Sin cuentas todavía, se ve igual.
    antes = await async_client.get(
        "/api/v1/organizations/my/whatsapp-numbers", headers=headers
    )
    assert [n["display_number"] for n in antes.json()] == ["51911223344"]

    conectada = await async_client.post(
        "/api/v1/organizations/my/ad-accounts",
        json={"platform": "META", "external_account_id": "112233",
              "name": "Primera", "access_token": "mock-token"},
        headers=headers,
    )
    assert conectada.status_code == 200

    # Y se sigue viendo con el filtro por cuenta ya aplicando.
    despues = await async_client.get(
        "/api/v1/organizations/my/whatsapp-numbers", headers=headers
    )
    assert [n["display_number"] for n in despues.json()] == ["51911223344"]


@pytest.mark.asyncio
async def test_un_numero_manual_no_se_puede_atar_a_una_cuenta_ajena(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El alcance de un número MANUAL lo fija el usuario, así que hay que
    validarlo: un id ajeno en el cuerpo de la request sería el leak entrando por
    la puerta de atrás."""
    org, ((acc_its, _, _), (acc_otro, _, _)) = await _dos_clientes_en_una_org(
        db_session, "scope-manual"
    )
    await db_session.commit()
    _, headers = await _usuario_de(
        async_client, db_session, org, "its5@example.com", [acc_its]
    )

    res = await async_client.post(
        "/api/v1/organizations/my/whatsapp-numbers",
        json={"display_number": "51955667788", "ad_account_ids": [acc_otro.id]},
        headers=headers,
    )
    assert res.status_code == 404
