"""Spec 02 — acumulación exacta y concurrencia contra PostgreSQL real.

Dos cosas que el motor embebido del resto de la suite no puede probar:

* **Acumulación exacta.** SQLite no tiene `NUMERIC` real: un `CAST(x AS
  NUMERIC)` sigue devolviendo coma flotante, así que la suma del gasto se
  acumula con el mismo error que se quiere evitar. En PostgreSQL el `cast` de
  `estado_del_mes` sí es exacto, y es ahí donde hay que comprobarlo.

* **Concurrencia real.** SQLite serializa las escrituras, así que la carrera que
  se quiere provocar no llega a ocurrir.

Se ejecutan solo con `TEST_POSTGRES_URL` fijada (ver
`tests/test_spec01_concurrencia_postgres.py`).
"""
import asyncio
import datetime
import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.budget_gate import (
    BudgetLimitExceeded, assert_cabe_en_el_mes, estado_del_mes, hoy_de_negocio,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Requiere PostgreSQL real: fijar TEST_POSTGRES_URL.",
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


async def _org_con_cuenta(Session, limite: str):
    async with Session() as db:
        org = Organization(slug=f"pg-{uuid.uuid4().hex[:8]}", name="Presupuesto")
        org.set_limite_mensual(limite)
        db.add(org)
        await db.flush()
        acc = AdAccount(
            organization_id=org.id, platform="META",
            external_account_id=f"act_{uuid.uuid4().hex[:8]}",
            name="Cuenta", status="ACTIVE", currency="PEN",
        )
        db.add(acc)
        await db.commit()
        return org.id, acc.id


@pytest.mark.asyncio
async def test_la_suma_del_gasto_es_exacta_sobre_cientos_de_snapshots(pg_sessionmaker):
    """Mil snapshots de un céntimo son diez soles, no 9.99999999999998.

    Es la forma en que el residuo binario llega de verdad a una compuerta
    financiera: no en una comparación suelta, sino acumulado sobre el histórico
    de un mes entero.
    """
    org_id, acc_id = await _org_con_cuenta(pg_sessionmaker, "10.00")
    hoy = hoy_de_negocio()

    async with pg_sessionmaker() as db:
        for i in range(1000):
            db.add(CampaignSnapshot(
                ad_account_id=acc_id, organization_id=org_id,
                campaign_id=f"c{i}", campaign_name="C", status="ACTIVE",
                spend=0.01, date=hoy,
            ))
        await db.commit()

        estado = await estado_del_mes(db, org_id)

    assert estado.gasto_real == Decimal("10.00")
    assert estado.margen == Decimal("0.00")

    # Y la suma equivalente en coma flotante NO da 10.00: es la diferencia que
    # decide si la compuerta cree que queda margen.
    acumulado = 0.0
    for _ in range(1000):
        acumulado += 0.01
    assert acumulado != 10.00


@pytest.mark.asyncio
async def test_c9_dos_autorizaciones_concurrentes_que_juntas_exceden(pg_sessionmaker):
    """Cada una cabe; juntas no.

    Con la comprobación resuelta por lectura, las dos transacciones leen el
    mismo consumo y las dos pasan. El resultado observable tiene que ser una
    sola autorización.
    """
    org_id, acc_id = await _org_con_cuenta(pg_sessionmaker, "1000.00")

    async def _autorizar(monto: str):
        async with pg_sessionmaker() as db:
            try:
                await assert_cabe_en_el_mes(db, org_id, monto)
            except BudgetLimitExceeded:
                return "rechazada"
            # El compromiso se materializa como gasto: es lo que hace que la
            # segunda transacción vea el consumo de la primera.
            db.add(CampaignSnapshot(
                ad_account_id=acc_id, organization_id=org_id,
                campaign_id=uuid.uuid4().hex, campaign_name="C", status="ACTIVE",
                spend=float(monto), date=hoy_de_negocio(),
            ))
            await db.commit()
            return "aceptada"

    resultados = await asyncio.gather(_autorizar("600.00"), _autorizar("600.00"))

    async with pg_sessionmaker() as db:
        estado = await estado_del_mes(db, org_id)

    assert estado.consumo <= estado.limite, (
        f"el consumo ({estado.consumo}) superó el límite ({estado.limite}): "
        f"resultados {resultados}"
    )
    assert resultados.count("aceptada") == 1
