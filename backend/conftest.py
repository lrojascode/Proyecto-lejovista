import os
import tempfile

# Defaults de entorno para tests. Se fijan ANTES de importar la app (que carga
# settings). Permiten correr `pytest` sin pasar variables a mano. Una ENCRYPTION_KEY
# Fernet válida de ejemplo (no es un secreto real).
os.environ.setdefault("SECRET_KEY", "test-secret-key-solo-para-tests-1234567890")
os.environ.setdefault("ENCRYPTION_KEY", "sHEU494awg7HtPuuIPg989QiYv0fUh0_kdmnGVa31N8=")
os.environ.setdefault("MOCK_SERVICES", "true")
os.environ.setdefault("ENABLE_META_WRITEBACK", "false")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from app.main import app
from app.database import Base, get_db

# SQLite en archivo (no :memory:) + NullPool: cada operación abre y cierra su propia
# conexión, evitando el binding de una conexión a un event loop concreto (que rompía
# los tests async con "MissingGreenlet"). El archivo permite compartir el esquema
# entre conexiones dentro de un mismo test.
_DB_PATH = os.path.join(tempfile.gettempdir(), "lejovista_test.db")
SQLALCHEMY_DATABASE_URL = f"sqlite+aiosqlite:///{_DB_PATH}"

engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
TestingSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def async_client() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def conceder_todas_las_cuentas(db, user_id: str, org_id: str) -> None:
    """Le concede a `user_id` todas las cuentas publicitarias de `org_id`.

    El acceso a una cuenta es una concesión explícita por usuario
    (`user_ad_accounts`) y es **fail-closed**: ser miembro de la organización ya
    no alcanza. En producción la concesión la crea el flujo que corresponda —
    conectar la cuenta, aceptar una invitación, o el seed de la migración que
    creó la tabla. Los tests que insertan `AdAccount` y `OrganizationUser` a
    mano se saltean los tres, así que necesitan este equivalente.

    Es lo que hace el seed de la migración: cada miembro conserva lo que ya
    veía. Un test que quiera probar el aislamiento concede a mano solo lo que
    corresponda, sin usar este helper.
    """
    from sqlalchemy import select
    from app.models.ad_account import AdAccount
    from app.models.user_ad_account import UserAdAccount

    ids = (await db.execute(
        select(AdAccount.id).where(AdAccount.organization_id == org_id)
    )).scalars().all()
    ya = set((await db.execute(
        select(UserAdAccount.ad_account_id).where(UserAdAccount.user_id == user_id)
    )).scalars().all())
    for acc_id in ids:
        if acc_id not in ya:
            db.add(UserAdAccount(
                user_id=user_id, ad_account_id=acc_id, organization_id=org_id,
            ))
    await db.flush()


async def conceder_a_los_miembros(db, org_id: str) -> None:
    """Concede cada cuenta de `org_id` a cada miembro de `org_id`.

    Exactamente lo que hace el seed de la migración que creó
    `user_ad_accounts`. Útil cuando el test crea la cuenta DESPUÉS del usuario
    y no tiene su id a mano.
    """
    from sqlalchemy import select
    from app.models.organization_user import OrganizationUser

    miembros = (await db.execute(
        select(OrganizationUser.user_id).where(
            OrganizationUser.organization_id == org_id
        )
    )).scalars().all()
    for user_id in miembros:
        await conceder_todas_las_cuentas(db, user_id, org_id)
