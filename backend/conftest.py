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
