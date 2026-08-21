"""Tests de la Fase 4 del plan de creación de campañas: descubrimiento de
página + WhatsApp durante el sync (MetaPage), para que la UI ofrezca un
dropdown en vez de pedir page_id/whatsapp_number a mano."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.services.meta_ads.sync import MetaAdsSyncService


async def _make_org_and_account(db: AsyncSession, slug="pages-org"):
    org = Organization(slug=slug, name="Pages Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Pages Acc", status="ACTIVE",
    )
    acc.set_access_token("valid-token")
    db.add(acc)
    await db.flush()
    return org, acc


class _PagesFakeLimiter:
    """Simula el descubrimiento de páginas acotado a la cuenta + el WhatsApp.

    La cuenta `act_1` pertenece al business `biz_propio`. El usuario del token
    ADEMÁS administra `biz_ajeno`, de otro cliente: sus páginas NO pueden
    aparecer (ver test_no_filtra_paginas_de_otros_clientes).
    """

    def __init__(self, wa_by_page=None, wa_error_pages=None, pages_error=False):
        self.wa_by_page = wa_by_page or {}
        self.wa_error_pages = wa_error_pages or set()
        self.pages_error = pages_error

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        if endpoint == "act_1" and params.get("fields") == "business":
            return {"id": "act_1", "business": {"id": "biz_propio", "name": "Cliente"}}
        if endpoint == "act_1/promote_pages":
            if self.pages_error:
                return {"error": {"message": "Invalid OAuth", "code": 190}}
            return {"data": [
                {"id": "page_1", "name": "Página Uno"},
                {"id": "page_2", "name": "Página Dos"},
            ]}
        if endpoint == "biz_propio/owned_pages":
            return {"data": [{"id": "page_1", "name": "Página Uno",
                              "connected_instagram_account": {"id": "ig_1"}}]}
        if endpoint == "biz_propio/client_pages":
            return {"data": []}
        # Página de OTRO cliente del mismo usuario del token.
        if endpoint.startswith("biz_ajeno/"):
            return {"data": [{"id": "page_ajena", "name": "Cliente Ajeno"}]}
        if endpoint == "me/accounts":
            if self.pages_error:
                return {"error": {"message": "Invalid OAuth", "code": 190}}
            return {"data": [
                {"id": "page_1", "name": "Página Uno", "category": "Business",
                 "connected_instagram_account": {"id": "ig_1"}},
                {"id": "page_2", "name": "Página Dos", "category": "Business"},
                {"id": "page_ajena", "name": "Cliente Ajeno", "category": "Business"},
            ]}
        if endpoint in self.wa_error_pages:
            return {"error": {"message": "Tried accessing nonexisting field", "code": 100}}
        if endpoint in self.wa_by_page:
            return {"whatsapp_number": self.wa_by_page[endpoint]}
        return {}

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        return await self.call_api(endpoint, params, method=method, access_token=access_token)


@pytest.mark.asyncio
async def test_sync_real_pages_populates_metapage_with_whatsapp(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)
    limiter = _PagesFakeLimiter(wa_by_page={"page_1": "51999888777", "page_2": "51988777666"})

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id).order_by(MetaPage.external_page_id)
    )).scalars().all()
    assert len(pages) == 2
    assert pages[0].name == "Página Uno"
    assert pages[0].instagram_actor_id == "ig_1"
    assert pages[0].whatsapp_number == "51999888777"
    assert pages[1].whatsapp_number == "51988777666"


@pytest.mark.asyncio
async def test_una_fuente_caida_no_tumba_el_descubrimiento(db_session: AsyncSession):
    """Con `promote_pages` en error, el business dueño sigue aportando. No
    lanza excepción y no se queda sin páginas por un fallo parcial."""
    org, acc = await _make_org_and_account(db_session, slug="pages-org-err")
    limiter = _PagesFakeLimiter(pages_error=True)

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id)
    )).scalars().all()
    assert {p.external_page_id for p in pages} == {"page_1"}


@pytest.mark.asyncio
async def test_sync_real_pages_keeps_page_when_whatsapp_field_unsupported(db_session: AsyncSession):
    """Lección del bug de customaudiences/approximate_count (HANDOFF 3.8): un
    campo no soportado en UNA llamada no debe tumbar el resto del descubrimiento."""
    org, acc = await _make_org_and_account(db_session, slug="pages-org-wa-fail")
    limiter = _PagesFakeLimiter(wa_error_pages={"page_1", "page_2"})

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id).order_by(MetaPage.external_page_id)
    )).scalars().all()
    assert len(pages) == 2
    assert pages[0].whatsapp_number is None
    assert pages[0].name == "Página Uno"  # el resto de los datos sí se guardó


@pytest.mark.asyncio
async def test_mock_sync_creates_mock_page(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="pages-org-mock")
    await db_session.commit()

    await MetaAdsSyncService(db_session).run_sync(acc, "FULL")

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id)
    )).scalars().all()
    assert len(pages) == 1
    assert pages[0].whatsapp_number == "51999888777"


# --- Descubrimiento multi-fuente (caso "Althea pack", 15-jul-2026): la página
# promocionable no salía en /me/accounts porque el usuario del token no tenía
# rol directo; ahora también se consultan los Business Manager y promote_pages. ---

class _MultiSourceFakeLimiter:
    """Simula las 4 fuentes de descubrimiento con solapamiento entre ellas."""

    def __init__(self, me_accounts_error=False):
        self.me_accounts_error = me_accounts_error

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        if endpoint == "me/accounts":
            if self.me_accounts_error:
                return {"error": {"message": "Invalid OAuth", "code": 190}}
            return {"data": [{"id": "page_own", "name": "Página Propia",
                              "connected_instagram_account": {"id": "ig_own"}}]}
        if endpoint == "act_1" and params.get("fields") == "business":
            return {"id": "act_1", "business": {"id": "biz_1", "name": "Mi Business"}}
        if endpoint == "me/businesses":
            # Ya no se usa: recorrerlo entero era el leak entre clientes.
            return {"data": [{"id": "biz_1", "name": "Mi Business"},
                             {"id": "biz_otro", "name": "Otro Cliente"}]}
        if endpoint.startswith("biz_otro/"):
            return {"data": [{"id": "page_ajena", "name": "Página de Otro Cliente"}]}
        if endpoint == "biz_1/owned_pages":
            # Solapa con me/accounts (page_own) + una nueva (page_althea).
            return {"data": [{"id": "page_own", "name": "Página Propia"},
                             {"id": "page_althea", "name": "Althea pack"}]}
        if endpoint == "biz_1/client_pages":
            return {"error": {"message": "Unsupported request", "code": 100}}
        if endpoint == "act_1/promote_pages":
            return {"data": [{"id": "page_promo", "name": "Página Promocionable"}]}
        if endpoint == "page_althea":
            return {"whatsapp_number": "51911222333"}
        return {}

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        return await self.call_api(endpoint, params, method=method, access_token=access_token)


@pytest.mark.asyncio
async def test_sync_real_pages_merges_business_and_promote_sources(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="pages-org-multi")
    limiter = _MultiSourceFakeLimiter()

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id).order_by(MetaPage.external_page_id)
    )).scalars().all()
    # Dedupe: page_own aparece en 2 fuentes pero se guarda 1 vez.
    # El caso "Althea pack" sigue cubierto: es una página que solo aparece en el
    # business DUEÑO de la cuenta, no en las fuentes directas.
    assert [p.external_page_id for p in pages] == ["page_althea", "page_own", "page_promo"]
    # Y la del OTRO cliente del mismo usuario del token no entra.
    assert "page_ajena" not in {p.external_page_id for p in pages}
    althea = pages[0]
    assert althea.name == "Althea pack"
    assert althea.whatsapp_number == "51911222333"
    # Un edge que falla (client_pages) no tumba las demás fuentes.
    own = pages[1]
    assert own.instagram_actor_id == "ig_own"


@pytest.mark.asyncio
async def test_sync_real_pages_continues_when_me_accounts_fails(db_session: AsyncSession):
    """Antes, un error en /me/accounts abortaba TODO el descubrimiento; ahora
    las otras fuentes siguen aportando páginas."""
    org, acc = await _make_org_and_account(db_session, slug="pages-org-partial")
    limiter = _MultiSourceFakeLimiter(me_accounts_error=True)

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id)
    )).scalars().all()
    ids = {p.external_page_id for p in pages}
    assert "page_althea" in ids and "page_promo" in ids
    assert "page_own" in ids  # vino por owned_pages aunque me/accounts falló


# --- `whatsapp_number` necesita el token de la PÁGINA (13-ago-2026): con el
# token de usuario Meta devuelve error aunque tenga whatsapp_business_management,
# así que el número quedaba SIEMPRE vacío y había que cargarlo a mano. ---

class _PageTokenFakeLimiter:
    """Registra con qué token se pidió el `whatsapp_number` de cada página.

    `/me/accounts` entrega token de página (si se pide en `fields`); las páginas
    que llegan por los Business Manager no lo traen, y ahí solo queda el token
    de usuario.
    """

    def __init__(self):
        self.wa_tokens = {}
        self.me_accounts_fields = ""

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        if endpoint == "me/accounts":
            self.me_accounts_fields = params.get("fields", "")
            return {"data": [{"id": "page_own", "name": "Página Propia",
                              "access_token": "PAGE-TOKEN-own"}]}
        if endpoint == "act_1" and params.get("fields") == "business":
            return {"id": "act_1", "business": {"id": "biz_1", "name": "Mi Business"}}
        if endpoint == "me/businesses":
            # Ya no se usa: recorrerlo entero era el leak entre clientes.
            return {"data": [{"id": "biz_1", "name": "Mi Business"},
                             {"id": "biz_otro", "name": "Otro Cliente"}]}
        if endpoint.startswith("biz_otro/"):
            return {"data": [{"id": "page_ajena", "name": "Página de Otro Cliente"}]}
        if endpoint == "biz_1/owned_pages":
            # `page_own` la descubre esta fuente (acotada al business dueño);
            # /me/accounts ya no descubre, solo aporta el token de página.
            return {"data": [{"id": "page_own", "name": "Página Propia"},
                             {"id": "page_biz", "name": "Página del Business"}]}
        if params.get("fields") == "whatsapp_number":
            self.wa_tokens[endpoint] = access_token
            if access_token.startswith("PAGE-TOKEN"):
                return {"whatsapp_number": "51999888777"}
            return {"error": {"message": "Tried accessing nonexisting field", "code": 100}}
        return {}

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        return await self.call_api(endpoint, params, method=method, access_token=access_token)


@pytest.mark.asyncio
async def test_whatsapp_number_uses_page_token_when_available(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="pages-org-page-token")
    limiter = _PageTokenFakeLimiter()

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    # El token de página solo viene si se pide explícitamente en `fields`.
    assert "access_token" in limiter.me_accounts_fields
    assert limiter.wa_tokens["page_own"] == "PAGE-TOKEN-own"

    page = (await db_session.execute(select(MetaPage).where(
        MetaPage.organization_id == org.id, MetaPage.external_page_id == "page_own",
    ))).scalar_one()
    assert page.whatsapp_number == "51999888777"


@pytest.mark.asyncio
async def test_whatsapp_number_falls_back_to_user_token_without_page_token(db_session: AsyncSession):
    """Las páginas descubiertas vía Business Manager / promote_pages no traen
    token propio: se intenta igual con el de usuario y, si falla, esa página
    queda sin número sin afectar a las demás."""
    org, acc = await _make_org_and_account(db_session, slug="pages-org-no-page-token")
    limiter = _PageTokenFakeLimiter()

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    assert limiter.wa_tokens["page_biz"] == "valid-token"

    page = (await db_session.execute(select(MetaPage).where(
        MetaPage.organization_id == org.id, MetaPage.external_page_id == "page_biz",
    ))).scalar_one()
    assert page.whatsapp_number is None
    assert page.name == "Página del Business"  # el resto sí se guardó


@pytest.mark.asyncio
async def test_sync_does_not_wipe_manual_whatsapp_number(db_session: AsyncSession):
    """El WhatsApp cargado a mano (PATCH /my/pages/{id}/whatsapp) NO debe
    borrarse cuando Meta no devuelve el campo (página sin número vinculado o
    token sin permiso) — antes el sync nocturno lo pisaba con None."""
    org, acc = await _make_org_and_account(db_session, slug="pages-org-keep-wa")
    page = MetaPage(
        organization_id=org.id, external_page_id="page_1", name="Página Uno",
        whatsapp_number="51900111222",  # cargado a mano
        status="ACTIVE",
    )
    db_session.add(page)
    await db_session.flush()

    # Meta devuelve la página pero SIN whatsapp_number (respuesta {} para el
    # fetch del número, como pasa en producción sin número vinculado).
    limiter = _PagesFakeLimiter(wa_by_page={})
    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    refreshed = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id,
                               MetaPage.external_page_id == "page_1")
    )).scalar_one()
    assert refreshed.whatsapp_number == "51900111222"


# --- Aislamiento entre clientes de una misma agencia (21-ago-2026) ---

class _AgenciaFakeLimiter:
    """Un usuario de agencia que administra VARIOS negocios.

    Es el caso real: un token correctamente acotado a la cuenta de un cliente,
    y la app mostrándole las páginas de los otros cuatro.
    """

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        if endpoint == "act_1" and params.get("fields") == "business":
            return {"id": "act_1", "business": {"id": "biz_cliente", "name": "ITS IN"}}
        if endpoint == "act_1/promote_pages":
            return {"data": [{"id": "page_cliente", "name": "ITS Institute"}]}
        if endpoint == "biz_cliente/owned_pages":
            return {"data": [{"id": "page_cliente", "name": "ITS Institute"}]}
        if endpoint == "biz_cliente/client_pages":
            return {"data": []}
        if endpoint == "me/businesses":
            # Los cinco negocios del usuario del token. Recorrerlos TODOS era el bug.
            return {"data": [
                {"id": "biz_cliente", "name": "ITS IN"},
                {"id": "biz_agencia", "name": "Agencia Kibo"},
                {"id": "biz_farma", "name": "Expert Farma"},
                {"id": "biz_edu", "name": "Círculo Educativo"},
            ]}
        if endpoint.startswith(("biz_agencia/", "biz_farma/", "biz_edu/")):
            return {"data": [{"id": f"page_{endpoint.split('/')[0]}", "name": "Página ajena"}]}
        if endpoint == "me/accounts":
            # El usuario tiene rol directo en páginas de varios clientes.
            return {"data": [
                {"id": "page_cliente", "name": "ITS Institute", "access_token": "PT"},
                {"id": "page_biz_agencia", "name": "Agencia Kibo", "access_token": "PT2"},
            ]}
        return {}

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        return await self.call_api(endpoint, params, method=method, access_token=access_token)


@pytest.mark.asyncio
async def test_no_filtra_paginas_de_otros_clientes(db_session: AsyncSession):
    """Caso real: token acotado a UNA cuenta, y a ese cliente le aparecían las
    páginas de los otros cuatro negocios de la agencia.

    El descubrimiento recorría `/me/businesses` entero —todos los negocios del
    USUARIO del token— más `/me/accounts` sin filtrar. Ahora se acota al
    business DUEÑO de la cuenta que se está sincronizando.
    """
    org, acc = await _make_org_and_account(db_session, slug="pages-agencia")

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", _AgenciaFakeLimiter())
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id)
    )).scalars().all()
    assert {p.external_page_id for p in pages} == {"page_cliente"}


@pytest.mark.asyncio
async def test_me_accounts_no_agrega_paginas_por_su_cuenta(db_session: AsyncSession):
    """`/me/accounts` sigue consultándose porque es lo ÚNICO que entrega el
    token de página (necesario para leer el WhatsApp), pero solo puede
    enriquecer páginas ya descubiertas por una fuente acotada."""
    org, acc = await _make_org_and_account(db_session, slug="pages-agencia-2")

    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", _AgenciaFakeLimiter())
    await db_session.flush()

    ids = {
        p.external_page_id for p in (await db_session.execute(
            select(MetaPage).where(MetaPage.organization_id == org.id)
        )).scalars().all()
    }
    assert "page_biz_agencia" not in ids


# --- Arreglar el descubrimiento no limpia lo que el bug ya guardó ---

@pytest.mark.asyncio
async def test_el_refresh_da_de_baja_las_paginas_que_ya_no_corresponden(
    db_session: AsyncSession, mocker
):
    """El descubrimiento viejo recorría TODOS los Business Manager del token,
    así que a ITS le aparecían las páginas de Expert Farma y Círculo Educativo.
    Acotarlo dejó de agregarlas — pero las cinco ya guardadas se quedaron ahí,
    porque el guardado es puro upsert y nada las saca.

    Un leak entre clientes que sobrevive a su propio arreglo sólo se limpia
    reconciliando: lo que no se ve, se da de baja.
    """
    from app.workers import tasks

    org, acc = await _make_org_and_account(db_session, "poda-org")
    for ext_id, nombre in [
        ("106686772334869", "ITS Institute"),
        ("861255400405184", "Agencia Kibo"),
        ("845080588695600", "Expert Farma"),
        ("312044271990101", "Círculo Educativo"),
    ]:
        db_session.add(MetaPage(organization_id=org.id, external_page_id=ext_id,
                                name=nombre, status="ACTIVE"))
    await db_session.commit()

    async def _solo_its(self, ad_account):
        return {"106686772334869"}

    mocker.patch.object(MetaAdsSyncService, "refresh_pages", _solo_its)

    await tasks.refresh_org_pages_impl(db_session, org.id)

    activas = (await db_session.execute(select(MetaPage).where(
        MetaPage.organization_id == org.id, MetaPage.status == "ACTIVE",
    ))).scalars().all()
    assert [p.external_page_id for p in activas] == ["106686772334869"]

    # No se borran: pueden estar referenciadas por campañas ya creadas.
    todas = (await db_session.execute(select(MetaPage).where(
        MetaPage.organization_id == org.id))).scalars().all()
    assert len(todas) == 4
