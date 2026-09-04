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
# El registro público está APAGADO en producción (spec 05): el alta de una
# organización pasa por el flujo asistido. La suite lo usa como fábrica de
# organizaciones en 37 archivos, así que se enciende sólo aquí. Que esté apagado
# por defecto se comprueba en `tests/test_spec05_experiencias.py`.
os.environ.setdefault("ENABLE_PUBLIC_SIGNUP", "true")

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

# Spec 14, criterio 2: la suite puede correr contra el motor de produccion.
# Con `TEST_DATABASE_URL` fijada, todo el archivo apunta a PostgreSQL; sin ella
# sigue usando SQLite. No son equivalentes y no se pretende que lo sean: SQLite
# es rapido y sirve para la parte de la suite que no toca el dialecto, y
# PostgreSQL es donde el dialecto, las restricciones y las carreras se comportan
# como en produccion. CI corre las dos.
#
# SQLite en archivo (no :memory:) + NullPool: cada operación abre y cierra su propia
# conexión, evitando el binding de una conexión a un event loop concreto (que rompía
# los tests async con "MissingGreenlet"). El archivo permite compartir el esquema
# entre conexiones dentro de un mismo test.
_DB_PATH = os.path.join(tempfile.gettempdir(), "lejovista_test.db")
_SQLITE_URL = f"sqlite+aiosqlite:///{_DB_PATH}"
SQLALCHEMY_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or _SQLITE_URL
CORRIENDO_SOBRE_POSTGRES = SQLALCHEMY_DATABASE_URL.startswith("postgresql")

engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
TestingSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)

# El motor de la aplicacion se sustituye ANTES de importar nada de `app`, para
# que los modulos que hacen `from app.database import async_session` arriba del
# todo se lleven el de test y no el de produccion.
#
# Sin esto, todo servicio que abre su propia sesion —en vez de recibir la del
# request— apuntaba a la base configurada en `.env`, que en una maquina de
# desarrollo no existe. No fallaba a la vista: la comprobacion de los
# interruptores operativos (spec 13) falla CERRADO, asi que apagaba la capacidad
# de IA y el chat devolvia una plantilla en lugar de la respuesta del modelo. El
# test correspondiente pasaba o fallaba segun otro test hubiera dejado el estado
# en la cache de 5 segundos, que es la peor forma de no fallar.
import app.database as _database  # noqa: E402

_database.engine = engine
_database.async_session = TestingSessionLocal

from app.main import app  # noqa: E402
from app.database import Base, get_db  # noqa: E402


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def mecanismo_de_revocacion_disponible():
    """Por defecto, los tests corren como si el mecanismo de revocación funcionara.

    La suite apunta `REDIS_URL` a un puerto muerto a propósito (ver
    `run-tests.sh`): el rate limit de `/auth/signup` es fail-open, así que sin
    Redis no limita y no hace falta aislar un Redis de test.

    Eso dejó de ser inocuo con la spec 01. Aprobar una acción, crear una campaña
    y escribir en Meta ahora fallan CERRADO cuando no se puede comprobar la
    validez de la sesión, así que un Redis ausente convertía media suite en
    503 — probando la caída, no el comportamiento.

    Este fixture fija el estado normal del sistema (mecanismo disponible, nada
    revocado). Los tests que quieren la caída la piden explícitamente con
    `mocker.patch`, que se aplica después y gana: ver
    `tests/test_spec01_seguridad_sesiones.py`, criterios 14 y 15.
    """
    async def _no_revocado(jti):
        return False

    async def _disponible():
        return True

    with patch("app.utils.security.is_revoked", side_effect=_no_revocado), \
         patch("app.utils.security.revocation_backend_available", side_effect=_disponible):
        yield


_codificacion_comprobada = False


async def _exigir_utf8(conn) -> None:
    """La base de test tiene que estar en UTF-8, como la de producción.

    Una base en SQL_ASCII acepta casi todo y revienta en el primer texto con un
    carácter fuera de ASCII — una raya, una comilla tipográfica, un mensaje de
    error de Meta — con un `UntranslatableCharacterError` que no se parece en
    nada a su causa. Un `initdb` con `LC_ALL=C`, que es lo que hace falta para
    levantar un clúster de pruebas en macOS, produce exactamente eso.

    Se comprueba una vez y se falla con el motivo, en vez de dejar que aparezca
    como un fallo de test aleatorio a mitad de la suite.
    """
    global _codificacion_comprobada
    if _codificacion_comprobada:
        return
    from sqlalchemy import text

    codificacion = (await conn.execute(
        text("SELECT pg_encoding_to_char(encoding) FROM pg_database "
             "WHERE datname = current_database()")
    )).scalar_one()
    if codificacion != "UTF8":
        raise RuntimeError(
            f"La base de test está en {codificacion} y producción usa UTF8. "
            f"Créala con: createdb -E UTF8 -T template0 --lc-collate=C --lc-ctype=C <nombre>"
        )
    _codificacion_comprobada = True


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    async with engine.begin() as conn:
        if CORRIENDO_SOBRE_POSTGRES:
            await _exigir_utf8(conn)
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # El catálogo de modelos de IA es DATO desde la spec 10, no código: sin
    # sembrarlo, el selector de modelos y la estimación de costo quedan vacíos y
    # los tests medirían un sistema que en producción no existe. Lo siembra la
    # migración allá y este fixture acá.
    from app.services.ai import catalogo
    async with TestingSessionLocal() as session:
        await catalogo.sembrar(session)
        await session.commit()
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


async def enlazar_paginas_y_numeros(db, org_id: str) -> None:
    """Enlaza cada página y cada número de `org_id` con cada cuenta de `org_id`.

    Las páginas y los números son por organización, pero su ALCANCE es por
    cuenta (`meta_page_ad_accounts`, `whatsapp_number_ad_accounts`): en una
    organización que aloja las cuentas de varios clientes, el dropdown de
    "Crear campaña" mostraba las de todos. En producción el enlace lo mantiene
    el sync —y lo poda— o el alta manual; el seed de la migración hizo esto
    mismo para lo que ya existía.

    Los tests que insertan `MetaPage`/`WhatsAppNumber` a mano se saltean los dos
    caminos, así que necesitan este equivalente. Un test que quiera probar el
    aislamiento enlaza a mano solo lo que corresponda.
    """
    from sqlalchemy import select
    from app.models.ad_account import AdAccount
    from app.models.meta_page import MetaPage
    from app.models.meta_page_ad_account import MetaPageAdAccount
    from app.models.whatsapp_number import WhatsAppNumber
    from app.models.whatsapp_number_ad_account import WhatsAppNumberAdAccount

    cuentas = (await db.execute(
        select(AdAccount.id).where(AdAccount.organization_id == org_id)
    )).scalars().all()
    if not cuentas:
        return

    for origen, enlace, columna in (
        (MetaPage, MetaPageAdAccount, "meta_page_id"),
        (WhatsAppNumber, WhatsAppNumberAdAccount, "whatsapp_number_id"),
    ):
        ids = (await db.execute(
            select(origen.id).where(origen.organization_id == org_id)
        )).scalars().all()
        ya = {
            (getattr(f, columna), f.ad_account_id)
            for f in (await db.execute(
                select(enlace).where(enlace.organization_id == org_id)
            )).scalars().all()
        }
        for propio_id in ids:
            for acc_id in cuentas:
                if (propio_id, acc_id) not in ya:
                    db.add(enlace(
                        **{columna: propio_id},
                        ad_account_id=acc_id,
                        organization_id=org_id,
                    ))
    await db.flush()


async def cablear_org(db, org_id: str) -> None:
    """Deja una organización de test en el estado que produce producción.

    Tres cosas que en la app las hacen tres flujos distintos —conectar una
    cuenta, aceptar una invitación, el sync de páginas— y que un test que
    inserta filas a mano se saltea las tres:

    * cada miembro tiene concedidas las cuentas de su organización;
    * cada página y cada número están al alcance de esas cuentas.
    """
    await conceder_a_los_miembros(db, org_id)
    await enlazar_paginas_y_numeros(db, org_id)


async def cuenta_de_prueba(db, org_id: str, external_account_id: str = "act_test"):
    """Una `AdAccount` ACTIVE, ya cableada con los miembros de la organización.

    Las páginas y los números existen POR una cuenta —el sync los descubre
    recorriendo lo que esa cuenta alcanza—, así que una organización sin
    ninguna cuenta no tiene de dónde sacarlos. Los tests que solo hacían signup
    y agregaban páginas describían un estado que producción no produce.
    """
    from app.models.ad_account import AdAccount

    acc = AdAccount(
        organization_id=org_id, platform="META",
        external_account_id=external_account_id, name="Cuenta de prueba",
        status="ACTIVE",
    )
    acc.set_access_token("mock-token")
    db.add(acc)
    await db.flush()
    await conceder_a_los_miembros(db, org_id)
    return acc
