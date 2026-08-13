"""Aislamiento multi-tenant del chat de creación de campañas (CH-1)."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_campaign_chat_flow import _mock_side_effects, _signup_with_ad_account_and_page  # noqa: F401


def _turn_id() -> str:
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_session_requires_auth(async_client: AsyncClient):
    res = await async_client.get("/api/v1/campaigns/chat/sessions/whatever")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_cannot_read_another_organizations_session(async_client: AsyncClient, db_session: AsyncSession):
    headers_a, org_a, ad_account_a = await _signup_with_ad_account_and_page(async_client, db_session, "chat-tenant-a")
    headers_b, org_b, _ = await _signup_with_ad_account_and_page(async_client, db_session, "chat-tenant-b")

    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_a}, headers=headers_a,
    )).json()["id"]

    res = await async_client.get(f"/api/v1/campaigns/chat/sessions/{session_id}", headers=headers_b)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_cannot_message_another_organizations_session(async_client: AsyncClient, db_session: AsyncSession):
    headers_a, org_a, ad_account_a = await _signup_with_ad_account_and_page(async_client, db_session, "chat-tenant-c")
    headers_b, org_b, _ = await _signup_with_ad_account_and_page(async_client, db_session, "chat-tenant-d")

    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_a}, headers=headers_a,
    )).json()["id"]

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "hola"},
        headers=headers_b,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_image_path_from_another_organization_is_rejected(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers_a, org_a, ad_account_a = await _signup_with_ad_account_and_page(async_client, db_session, "chat-tenant-e")
    _, org_b, _ = await _signup_with_ad_account_and_page(async_client, db_session, "chat-tenant-f")

    session_id = (await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_a}, headers=headers_a,
    )).json()["id"]
    await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "text": "vender termas"},
        headers=headers_a,
    )

    res = await async_client.post(
        f"/api/v1/campaigns/chat/sessions/{session_id}/messages",
        json={"client_turn_id": _turn_id(), "image_path": f"{org_b}/ajena.jpg"},
        headers=headers_a,
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_session_is_pinned_to_the_ad_account_it_was_created_with(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, ad_account_id = await _signup_with_ad_account_and_page(
        async_client, db_session, "chat-tenant-pin"
    )
    res = await async_client.post(
        "/api/v1/campaigns/chat/sessions", json={"ad_account_id": ad_account_id}, headers=headers,
    )
    session = res.json()
    assert session["ad_account_id"] == ad_account_id
