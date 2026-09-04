"""Spec 01 — carreras de invitación y de membresía contra PostgreSQL real.

Estos criterios (7 y 10) NO se pueden comprobar de verdad contra el motor
embebido que usa el resto de la suite: la restricción única y el `UPDATE`
condicional se comportan distinto, y SQLite serializa las escrituras, así que la
carrera que se quiere provocar no llega a ocurrir.

Se ejecutan solo cuando hay una base de PostgreSQL disponible:

    TEST_POSTGRES_URL=postgresql+asyncpg://user@host:puerto/basedetest pytest \\
        tests/test_spec01_concurrencia_postgres.py

Sin esa variable se saltan. Desde la spec 14 CI la fija y corren en cada
construccion; fuera de CI se corren a mano y su ausencia es visible como
`skipped`, no como verde.
"""
import asyncio
import datetime
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.models.invitation import Invitation, hash_invitation_token
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Requiere PostgreSQL real: fijar TEST_POSTGRES_URL (ver docstring).",
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


async def _org_y_usuario(Session):
    async with Session() as db:
        org = Organization(slug=f"conc-{uuid.uuid4().hex[:8]}", name="Concurrencia")
        db.add(org)
        await db.flush()
        user = User(
            email=f"u-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            full_name="Concurrente",
        )
        db.add(user)
        await db.commit()
        return org.id, user.id


@pytest.mark.asyncio
async def test_c10_dos_membresias_concurrentes_dejan_una_sola(pg_sessionmaker):
    """La comprobación "¿ya existe?" de la aplicación la hacen las DOS
    transacciones antes de que ninguna escriba. Solo la base puede decidir."""
    org_id, user_id = await _org_y_usuario(pg_sessionmaker)

    async def _insertar(rol: str):
        async with pg_sessionmaker() as db:
            db.add(OrganizationUser(user_id=user_id, organization_id=org_id, role=rol))
            try:
                await db.commit()
                return "ok"
            except IntegrityError:
                await db.rollback()
                return "rechazada"

    resultados = await asyncio.gather(_insertar("ADMIN"), _insertar("VIEWER"))

    assert sorted(resultados) == ["ok", "rechazada"]
    async with pg_sessionmaker() as db:
        filas = (await db.execute(
            select(OrganizationUser).where(OrganizationUser.user_id == user_id)
        )).scalars().all()
    assert len(filas) == 1


@pytest.mark.asyncio
async def test_c7_dos_consumos_concurrentes_de_la_invitacion(pg_sessionmaker):
    """El `UPDATE` condicional es una sola operación: comprueba y marca a la
    vez. Exactamente una de las dos transacciones cambia una fila."""
    org_id, _ = await _org_y_usuario(pg_sessionmaker)
    token = "token-de-carrera"

    async with pg_sessionmaker() as db:
        db.add(Invitation(
            organization_id=org_id,
            email="invitado@example.com",
            role="OPERATOR",
            token_hash=hash_invitation_token(token),
            expires_at=datetime.datetime.utcnow() + datetime.timedelta(days=7),
        ))
        await db.commit()

    async def _consumir():
        async with pg_sessionmaker() as db:
            inv = (await db.execute(
                select(Invitation).where(
                    Invitation.token_hash == hash_invitation_token(token)
                )
            )).scalar_one()
            res = await db.execute(
                update(Invitation)
                .where(Invitation.id == inv.id, Invitation.accepted_at.is_(None))
                .values(accepted_at=datetime.datetime.utcnow())
            )
            await db.commit()
            return res.rowcount

    filas_cambiadas = await asyncio.gather(_consumir(), _consumir())
    assert sorted(filas_cambiadas) == [0, 1], (
        "exactamente una de las dos aceptaciones consume la invitación"
    )
