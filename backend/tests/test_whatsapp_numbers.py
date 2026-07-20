"""Catálogo de números de WhatsApp (F1).

Dos carencias reales: cada página guardaba UN número y la UI solo mostraba el
campo mientras estuviera vacío, así que un número mal tipeado quedaba
congelado para siempre.
"""
import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.whatsapp_number import WhatsAppNumber


async def _signup(async_client, slug):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"{slug}@test.com", "password": "Password123!",
        "full_name": "WA Tester", "organization_slug": slug,
        "organization_name": "WA Org",
    })
    body = res.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


async def _org_id(db_session, slug):
    return (await db_session.execute(
        select(Organization.id).where(Organization.slug == slug)
    )).scalar_one()


@pytest.mark.asyncio
async def test_add_and_list_number(async_client):
    headers = await _signup(async_client, "wa-add")

    res = await async_client.post("/api/v1/organizations/my/whatsapp-numbers",
                                  headers=headers, json={"display_number": "51999888777"})
    assert res.status_code == 201
    assert res.json()["source"] == "MANUAL"

    listed = await async_client.get("/api/v1/organizations/my/whatsapp-numbers", headers=headers)
    assert [n["display_number"] for n in listed.json()] == ["51999888777"]


@pytest.mark.asyncio
async def test_number_can_be_edited_after_saving(async_client):
    """El motivo de existir de la feature: antes no había forma de corregirlo."""
    headers = await _signup(async_client, "wa-edit")
    created = await async_client.post("/api/v1/organizations/my/whatsapp-numbers",
                                      headers=headers, json={"display_number": "51999000111"})
    number_id = created.json()["id"]

    res = await async_client.patch(
        f"/api/v1/organizations/my/whatsapp-numbers/{number_id}",
        headers=headers, json={"display_number": "51999000222"},
    )
    assert res.status_code == 200
    assert res.json()["display_number"] == "51999000222"


@pytest.mark.asyncio
async def test_duplicate_number_is_rejected(async_client):
    headers = await _signup(async_client, "wa-dup")
    payload = {"display_number": "51999333444"}
    await async_client.post("/api/v1/organizations/my/whatsapp-numbers", headers=headers, json=payload)

    res = await async_client.post("/api/v1/organizations/my/whatsapp-numbers",
                                  headers=headers, json=payload)
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_delete_number(async_client):
    headers = await _signup(async_client, "wa-del")
    created = await async_client.post("/api/v1/organizations/my/whatsapp-numbers",
                                      headers=headers, json={"display_number": "51999555666"})

    res = await async_client.delete(
        f"/api/v1/organizations/my/whatsapp-numbers/{created.json()['id']}", headers=headers)
    assert res.status_code == 200

    listed = await async_client.get("/api/v1/organizations/my/whatsapp-numbers", headers=headers)
    assert listed.json() == []


@pytest.mark.asyncio
async def test_meta_sourced_numbers_are_read_only(async_client, db_session):
    """Los descubiertos en Meta los reescribe el sync: editarlos o borrarlos a
    mano daría la falsa impresión de que el cambio persiste."""
    headers = await _signup(async_client, "wa-meta")
    org_id = await _org_id(db_session, "wa-meta")

    row = WhatsAppNumber(organization_id=org_id, display_number="51900111222",
                         source="META", external_id="phone_1")
    db_session.add(row)
    await db_session.commit()

    patched = await async_client.patch(
        f"/api/v1/organizations/my/whatsapp-numbers/{row.id}",
        headers=headers, json={"display_number": "51900111333"},
    )
    assert patched.status_code == 400

    deleted = await async_client.delete(
        f"/api/v1/organizations/my/whatsapp-numbers/{row.id}", headers=headers)
    assert deleted.status_code == 400


@pytest.mark.asyncio
async def test_numbers_are_scoped_to_the_organization(async_client):
    headers_a = await _signup(async_client, "wa-org-a")
    headers_b = await _signup(async_client, "wa-org-b")

    created = await async_client.post("/api/v1/organizations/my/whatsapp-numbers",
                                      headers=headers_a, json={"display_number": "51988777666"})

    # La otra organización ni lo ve ni lo puede tocar.
    listed_b = await async_client.get("/api/v1/organizations/my/whatsapp-numbers", headers=headers_b)
    assert listed_b.json() == []

    res = await async_client.patch(
        f"/api/v1/organizations/my/whatsapp-numbers/{created.json()['id']}",
        headers=headers_b, json={"display_number": "51988777000"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_saving_page_whatsapp_also_fills_the_catalog(async_client, db_session):
    """Un número cargado para una página sirve para cualquier campaña: no debe
    haber que escribirlo dos veces."""
    from app.models.meta_page import MetaPage

    headers = await _signup(async_client, "wa-page")
    org_id = await _org_id(db_session, "wa-page")

    page = MetaPage(organization_id=org_id, external_page_id="page_1", name="Página", status="ACTIVE")
    db_session.add(page)
    await db_session.commit()

    await async_client.patch(f"/api/v1/organizations/my/pages/{page.id}/whatsapp",
                             headers=headers, json={"whatsapp_number": "51977666555"})

    listed = await async_client.get("/api/v1/organizations/my/whatsapp-numbers", headers=headers)
    assert [n["display_number"] for n in listed.json()] == ["51977666555"]
