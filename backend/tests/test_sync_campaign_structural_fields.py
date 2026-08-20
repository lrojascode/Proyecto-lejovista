"""Punto 1 del plan de arreglos: objective/created_time/start_time/stop_time de
campaña (y end_time de adset) nunca se pedían a Meta, así que el chat no podía
responder ni "¿qué tipo de campaña es?" ni "¿cuándo fue creada?". Estos tests
cubren el parseo de fechas ISO 8601 con offset, que un campo ausente no rompa
el sync, y que un solo sync ponga al día TODOS los snapshots históricos de la
campaña (no solo el del día)."""
import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.adset_snapshot import AdsetSnapshot
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.meta_ads.sync import MetaAdsSyncService


async def _make_org_and_account(db: AsyncSession, slug="struct-sync-org"):
    org = Organization(slug=slug, name="Struct Sync Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Struct Sync Acc", status="ACTIVE",
    )
    acc.set_access_token("valid-token")
    db.add(acc)
    await db.flush()
    return org, acc


class _StructuralFieldsLimiter:
    """Campañas y adsets con objective/created_time/start_time/stop_time/end_time,
    tal como los devuelve Meta (offset sin dos puntos, `-0500`)."""

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        d = "2026-07-02"
        if endpoint.endswith("/insights"):
            if params.get("breakdowns"):
                return {"data": []}
            level = params.get("level")
            msg = [{"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "20"}]
            if not level:
                # Histórico mensual de gasto (_sync_spend_history): sin `level`.
                return {"data": []}
            if level == "campaign":
                return {"data": [{"campaign_id": "c1", "campaign_name": "Camp 1", "date_start": d,
                                  "spend": "50.0", "impressions": "5000", "reach": "4000", "clicks": "90",
                                  "frequency": "1.25", "actions": msg}]}
            if level == "adset":
                return {"data": [{"adset_id": "as1", "adset_name": "Adset 1", "campaign_id": "c1", "date_start": d,
                                  "spend": "30.0", "impressions": "3000", "reach": "2500", "clicks": "55",
                                  "actions": msg}]}
            if level == "ad":
                return {"data": []}
        if endpoint.endswith("/campaigns"):
            return {"data": [{
                "id": "c1", "name": "Camp 1", "status": "ACTIVE", "daily_budget": "7000",
                "objective": "OUTCOME_ENGAGEMENT",
                "created_time": "2026-01-15T10:30:00-0500",
                "start_time": "2026-01-16T00:00:00-0500",
                # stop_time ausente a propósito: Meta no siempre lo devuelve.
            }]}
        if endpoint.endswith("/adsets"):
            return {"data": [{
                "id": "as1", "name": "Adset 1", "campaign_id": "c1", "status": "ACTIVE",
                "targeting": {},
                "end_time": "2026-08-01T23:59:00-0500",
            }]}
        if endpoint.endswith("/ads"):
            return {"data": []}
        if endpoint.endswith("/adimages"):
            return {"data": []}
        if endpoint.endswith("/customaudiences"):
            return {"data": []}
        return {"amount_spent": "10000", "spend_cap": "500000", "account_status": 1}

    async def call_api_paginated(self, endpoint, params, method="GET", access_token="", max_pages=50):
        return await self.call_api(endpoint, params, method=method, access_token=access_token)


@pytest.mark.asyncio
async def test_real_sync_captures_objective_and_dates(db_session: AsyncSession):
    org, acc = await _make_org_and_account(db_session)

    await MetaAdsSyncService(db_session)._sync_real_data(acc, "INCREMENTAL", _StructuralFieldsLimiter())

    camp = (await db_session.execute(
        select(CampaignSnapshot).where(CampaignSnapshot.ad_account_id == acc.id, CampaignSnapshot.campaign_id == "c1")
    )).scalar_one()
    assert camp.objective == "OUTCOME_ENGAGEMENT"
    # 2026-01-15T10:30:00-0500 -> 15:30 UTC
    assert camp.created_time == datetime.datetime(2026, 1, 15, 15, 30, 0)
    assert camp.start_time == datetime.datetime(2026, 1, 16, 5, 0, 0)
    assert camp.stop_time is None  # campo ausente: no rompe el sync

    adset = (await db_session.execute(
        select(AdsetSnapshot).where(AdsetSnapshot.ad_account_id == acc.id, AdsetSnapshot.adset_id == "as1")
    )).scalar_one()
    assert adset.end_time == datetime.datetime(2026, 8, 2, 4, 59, 0)


@pytest.mark.asyncio
async def test_real_sync_backfills_structural_fields_on_existing_historical_snapshots(db_session: AsyncSession):
    """Un snapshot histórico ya guardado (sincronizado antes de este arreglo, o
    en un día sin insight) también debe quedar al día con un solo sync — sin
    tener que recorrer 180 días de historial."""
    org, acc = await _make_org_and_account(db_session, slug="struct-backfill-org")
    old_day = datetime.date.today() - datetime.timedelta(days=10)
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id,
        campaign_id="c1", campaign_name="Camp 1", status="ACTIVE", budget=70.0, spend=70.0,
        date=old_day,
    ))
    await db_session.commit()

    await MetaAdsSyncService(db_session)._sync_real_data(acc, "INCREMENTAL", _StructuralFieldsLimiter())

    old_snap = (await db_session.execute(
        select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == acc.id,
            CampaignSnapshot.campaign_id == "c1",
            CampaignSnapshot.date == old_day,
        )
    )).scalar_one()
    assert old_snap.objective == "OUTCOME_ENGAGEMENT"
    assert old_snap.created_time == datetime.datetime(2026, 1, 15, 15, 30, 0)


def test_parse_meta_datetime_handles_offset_without_colon_and_missing_values():
    parse = MetaAdsSyncService._parse_meta_datetime
    assert parse("2026-01-15T10:30:00-0500") == datetime.datetime(2026, 1, 15, 15, 30, 0)
    assert parse(None) is None
    assert parse("") is None
    assert parse("not-a-date") is None
