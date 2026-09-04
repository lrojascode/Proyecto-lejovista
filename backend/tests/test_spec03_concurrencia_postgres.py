"""Spec 03 — consumo concurrente de una autorización contra PostgreSQL real.

El criterio 5 dice: "Dos peticiones de ejecución concurrentes sobre la misma
aprobación producen una sola Saga y un solo conjunto de identificadores de Meta".
Eso solo se puede comprobar donde la carrera ocurre de verdad: SQLite serializa
las escrituras, así que el `UPDATE` condicional nunca compite consigo mismo.

Se ejecuta solo con `TEST_POSTGRES_URL` fijada.
"""
import asyncio
import datetime
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_proposal import (
    APPROVAL_CONSUMED, CampaignApproval, CampaignProposalVersion, MetaWriteIntent,
)
from app.models.organization import Organization
from app.services.proposal_versioning import (
    AprobacionInvalida, consumir_aprobacion, registrar_intencion,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="Requiere PostgreSQL real: fijar TEST_POSTGRES_URL."
)


@pytest_asyncio.fixture
async def pg_sessionmaker():
    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _propuesta_aprobada(Session):
    async with Session() as db:
        org = Organization(slug=f"pg3-{uuid.uuid4().hex[:8]}", name="Idempotencia")
        db.add(org)
        await db.flush()
        draft = CampaignDraft(
            organization_id=org.id, name="Curso", payload={}, status="PENDING_CREATION"
        )
        db.add(draft)
        await db.flush()
        version = CampaignProposalVersion(
            draft_id=draft.id, organization_id=org.id, ad_account_id="acc-1",
            version_number=1, config_json={}, material_fingerprint="f" * 64,
        )
        db.add(version)
        await db.flush()
        aprobacion = CampaignApproval(
            version_id=version.id, draft_id=draft.id, organization_id=org.id
        )
        db.add(aprobacion)
        await db.commit()
        return org.id, draft.id, aprobacion.id


@pytest.mark.asyncio
async def test_c5_dos_ejecuciones_concurrentes_consumen_la_aprobacion_una_vez(
    pg_sessionmaker
):
    """El `UPDATE` condicional hace la comprobación y la marca en una sola
    operación. Que la segunda reciba un error y no una Saga es lo que impide la
    campaña duplicada."""
    org_id, draft_id, approval_id = await _propuesta_aprobada(pg_sessionmaker)

    async def _consumir():
        async with pg_sessionmaker() as db:
            try:
                await consumir_aprobacion(db, approval_id)
                await db.commit()
                return "consumida"
            except AprobacionInvalida as e:
                return e.code

    resultados = await asyncio.gather(_consumir(), _consumir())

    assert resultados.count("consumida") == 1, resultados
    assert "APPROVAL_ALREADY_CONSUMED" in resultados

    async with pg_sessionmaker() as db:
        aprobacion = await db.get(CampaignApproval, approval_id)
        assert aprobacion.status == APPROVAL_CONSUMED


@pytest.mark.asyncio
async def test_dos_registros_concurrentes_comparten_una_sola_intencion(pg_sessionmaker):
    """La clave de idempotencia es única en la base: dos pasos que compiten no
    pueden producir dos intenciones para la misma operación, que es lo que
    permitiría dos POST."""
    org_id, draft_id, approval_id = await _propuesta_aprobada(pg_sessionmaker)

    async def _registrar():
        async with pg_sessionmaker() as db:
            try:
                intencion, ya = await registrar_intencion(
                    db, approval_id=approval_id, operacion="create_campaign",
                    organization_id=org_id, ad_account_id="acc-1", draft_id=draft_id,
                    correlation_tag="MARCA_MSJ_Curso_AGO2026",
                )
                await db.commit()
                return intencion.idempotency_key
            except IntegrityError:
                await db.rollback()
                return "duplicada"

    await asyncio.gather(_registrar(), _registrar())

    async with pg_sessionmaker() as db:
        filas = (await db.execute(
            select(MetaWriteIntent).where(MetaWriteIntent.approval_id == approval_id)
        )).scalars().all()
    assert len(filas) == 1, "una sola intención por operación y autorización"
