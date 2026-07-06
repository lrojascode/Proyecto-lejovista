"""Tests de la captura del copy real del anuncio ganador durante el sync
(extensión del plan de creación de campañas, pedida tras usar la app en vivo):
antes solo se guardaba la imagen (MetaCreative); ahora _sync_real_ads también
captura title/body/call_to_action_type del creativo, para que la IA pueda
inspirarse en el texto real que ya funcionó (Fase 3B)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ad_snapshot import AdSnapshot
from app.services.meta_ads.sync import MetaAdsSyncService


async def _make_org_and_account(db: AsyncSession, slug="copy-sync-org"):
    org = Organization(slug=slug, name="Copy Sync Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Copy Sync Acc", status="ACTIVE",
    )
    acc.set_access_token("valid-token")
    db.add(acc)
    await db.flush()
    return org, acc


class _CreativeCopyFakeLimiter:
    """Simula /ads devolviendo title/body/call_to_action_type reales del creativo,
    y una insight simple a nivel ad para generar el AdSnapshot correspondiente."""

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        if endpoint.endswith("/insights") and params.get("level") == "ad":
            return {"data": [{
                "ad_id": "ad1", "ad_name": "Ad Ganador", "adset_id": "as1", "campaign_id": "c1",
                "date_start": "2026-07-02", "spend": "40.0", "impressions": "3000",
                "reach": "2500", "clicks": "55",
                "actions": [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "25"}],
            }]}
        if endpoint.endswith("/ads"):
            return {"data": [{
                "id": "ad1",
                "creative": {
                    "id": "cr1",
                    "title": "El copy que ya funcionó",
                    "body": "Cuerpo real del anuncio ganador con gancho probado",
                    "call_to_action_type": "WHATSAPP_MESSAGE",
                },
            }]}
        return {"data": []}

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        return await self.call_api(endpoint, params, method=method, access_token=access_token)


@pytest.mark.asyncio
async def test_sync_real_ads_captures_creative_copy(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)

    await MetaAdsSyncService(db_session)._sync_real_ads(acc, "valid-token", _CreativeCopyFakeLimiter(), "last_30d")
    await db_session.flush()

    snap = (await db_session.execute(
        select(AdSnapshot).where(AdSnapshot.ad_account_id == acc.id, AdSnapshot.ad_id == "ad1")
    )).scalar_one()

    assert snap.creative_id == "cr1"
    assert snap.creative_title == "El copy que ya funcionó"
    assert snap.creative_body == "Cuerpo real del anuncio ganador con gancho probado"
    assert snap.creative_cta_type == "WHATSAPP_MESSAGE"
