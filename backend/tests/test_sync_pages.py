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
    """Simula /me/accounts + GET de whatsapp_number por página."""

    def __init__(self, wa_by_page=None, wa_error_pages=None, pages_error=False):
        self.wa_by_page = wa_by_page or {}
        self.wa_error_pages = wa_error_pages or set()
        self.pages_error = pages_error

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        if endpoint == "me/accounts":
            if self.pages_error:
                return {"error": {"message": "Invalid OAuth", "code": 190}}
            return {"data": [
                {"id": "page_1", "name": "Página Uno", "category": "Business",
                 "connected_instagram_account": {"id": "ig_1"}},
                {"id": "page_2", "name": "Página Dos", "category": "Business"},
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
async def test_sync_real_pages_soft_fails_when_me_accounts_errors(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session, slug="pages-org-err")
    limiter = _PagesFakeLimiter(pages_error=True)

    # No debe lanzar excepción — solo loguear y no crear páginas.
    await MetaAdsSyncService(db_session)._sync_real_pages(acc, "valid-token", limiter)
    await db_session.flush()

    pages = (await db_session.execute(
        select(MetaPage).where(MetaPage.organization_id == org.id)
    )).scalars().all()
    assert pages == []


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
        if endpoint == "me/businesses":
            return {"data": [{"id": "biz_1", "name": "Mi Business"}]}
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
    assert [p.external_page_id for p in pages] == ["page_althea", "page_own", "page_promo"]
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
