"""Carrera entre dos syncs de la misma cuenta y sus secuelas (incidente
2026-07-17, cuenta Agencia Kibo).

Reconectar la cuenta dispara un sync FULL; un INCREMENTAL manual 7 s después
corrió EN PARALELO (el worker `sync` tiene concurrency 2). Ambos hicieron
"¿existe este creativo? no → insertar" y el segundo reventó con
UniqueViolationError (uq_creative_acc_hash). Peor: el commit final de run_sync
estaba FUERA del try, así que ese error escapaba sin marcar el SyncJob, que
quedaba en RUNNING para siempre (3 jobs fantasma en la BD lo confirmaron).
"""
import concurrent.futures

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.sync_job import SyncJob
from app.services.meta_ads.sync import MetaAdsSyncService
from app.workers.tasks import sync_ad_account


def run_in_thread(func, *args, **kwargs):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return executor.submit(func, *args, **kwargs).result()


class AsyncContextManagerMock:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture(autouse=True)
def mock_db_session(mocker, db_session):

# Spec 14: la tarea corre en OTRO hilo, con su propio bucle de eventos. Pasarle
# la sesion del test —creada en el bucle de pytest— funcionaba con el motor
# embebido y revienta contra PostgreSQL: la conexion de asyncpg queda atada a un
# bucle y el cierre ocurre en otro. Se le da una FABRICA, que es lo que la tarea
# usa en produccion: abre su propia sesion en el bucle donde corre.
    from conftest import TestingSessionLocal

    mocker.patch("app.workers.tasks.async_session", TestingSessionLocal)


@pytest.fixture
async def account(init_db, db_session: AsyncSession):
    org = Organization(slug="sync-race-org", name="Sync Race Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_race", name="Race Acc", status="ACTIVE",
    )
    acc.set_access_token("tok")
    db_session.add(acc)
    await db_session.commit()
    return org, acc


# --- El SyncJob nunca debe quedarse en RUNNING ---

@pytest.mark.asyncio
async def test_sync_job_marked_failed_when_flush_raises_integrity_error(
    account, db_session: AsyncSession, mocker
):
    """Antes: la IntegrityError del volcado final escapaba (commit fuera del try)
    y el job quedaba RUNNING para siempre. Ahora se revierte la sesión y el job
    queda FAILED con el motivo."""
    org, acc = account
    mocker.patch.object(
        MetaAdsSyncService, "_sync_mock_data", autospec=True,
        side_effect=IntegrityError(
            "INSERT INTO meta_creatives ...", {},
            Exception('duplicate key value violates unique constraint "uq_creative_acc_hash"'),
        ),
    )

    job = await MetaAdsSyncService(db_session).run_sync(acc, "INCREMENTAL")

    assert job.status == "FAILED"
    assert "uq_creative_acc_hash" in (job.error_message or "")
    assert job.completed_at is not None

    # Y queda persistido como FAILED, no RUNNING (el fantasma del incidente).
    persisted = (await db_session.execute(
        select(SyncJob).where(SyncJob.ad_account_id == acc.id)
    )).scalars().all()
    assert [j.status for j in persisted] == ["FAILED"]


@pytest.mark.asyncio
async def test_sync_job_completes_normally(account, db_session: AsyncSession):
    """Camino feliz intacto tras mover el commit final dentro del try."""
    org, acc = account

    job = await MetaAdsSyncService(db_session).run_sync(acc, "INCREMENTAL")

    assert job.status == "COMPLETED"
    assert job.completed_at is not None


# --- Lock por cuenta: no dos syncs en paralelo sobre la misma ---

@pytest.mark.asyncio
async def test_second_concurrent_sync_is_skipped(account, mocker):
    """Si ya hay un sync corriendo para la cuenta, el segundo se omite en vez de
    correr en paralelo y pisarse en los upserts."""
    org, acc = account
    mocker.patch("app.workers.tasks._acquire_sync_lock", return_value=False)
    mock_run_sync = mocker.patch(
        "app.services.meta_ads.sync.MetaAdsSyncService.run_sync", autospec=True
    )

    run_in_thread(sync_ad_account, acc.id, "INCREMENTAL")

    mock_run_sync.assert_not_called()


@pytest.mark.asyncio
async def test_sync_lock_released_after_run(account, mocker):
    org, acc = account
    mocker.patch("app.workers.tasks._acquire_sync_lock", return_value=True)
    mock_release = mocker.patch("app.workers.tasks._release_sync_lock")
    mocker.patch("app.workers.tasks._schedule_org_analysis_debounced")
    mocker.patch(
        "app.services.meta_ads.sync.MetaAdsSyncService.run_sync", autospec=True,
        return_value=type("J", (), {"status": "COMPLETED", "records_synced": 3, "id": "j1"})(),
    )

    run_in_thread(sync_ad_account, acc.id, "INCREMENTAL")

    mock_release.assert_called_once_with(acc.id)


@pytest.mark.asyncio
async def test_sync_lock_released_even_when_sync_raises(account, mocker):
    """El lock debe soltarse aunque el sync falle — si no, un TransientMetaError
    dejaría la cuenta bloqueada y el reintento de Celery no podría tomarlo."""
    org, acc = account
    mocker.patch("app.workers.tasks._acquire_sync_lock", return_value=True)
    mock_release = mocker.patch("app.workers.tasks._release_sync_lock")
    mocker.patch(
        "app.services.meta_ads.sync.MetaAdsSyncService.run_sync", autospec=True,
        side_effect=RuntimeError("boom"),
    )

    with pytest.raises(RuntimeError):
        run_in_thread(sync_ad_account, acc.id, "INCREMENTAL")

    mock_release.assert_called_once_with(acc.id)


def test_acquire_sync_lock_fails_open_without_redis(mocker):
    """Fail-open: si Redis no responde se sincroniza igual (preferimos arriesgar
    la carrera antes que dejar de sincronizar)."""
    from app.workers import tasks

    mocker.patch("redis.Redis.from_url", side_effect=Exception("redis caído"))
    assert tasks._acquire_sync_lock("acc-1") is True
