"""D-1: debounce del análisis post-sync.

Una org con N cuentas disparaba N análisis completos (cada uno itera todas las
cuentas y llama al LLM por cada una). Con el lock Redis (SET NX EX), solo el
primer sync que completa agenda UN análisis diferido; los demás no encolan nada.
Fail-open: si Redis no responde, se encola igual.
"""
import pytest

from app.workers import tasks as worker_tasks


class _FakeRedis:
    """SET NX en memoria, suficiente para el lock del debounce."""

    def __init__(self):
        self.store = {}
        self.deleted = []

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None  # como redis-py: NX sobre clave existente -> None
        self.store[key] = value
        return True

    def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)

    def close(self):
        pass


class _BrokenRedis:
    def set(self, *a, **k):
        raise ConnectionError("redis caído")

    def close(self):
        pass


@pytest.fixture
def fake_redis(mocker):
    fake = _FakeRedis()
    mocker.patch("redis.Redis.from_url", return_value=fake)
    return fake


@pytest.fixture
def mock_apply_async(mocker):
    return mocker.patch.object(worker_tasks.run_ai_analysis, "apply_async")


def test_first_sync_schedules_deferred_analysis(fake_redis, mock_apply_async):
    scheduled = worker_tasks._schedule_org_analysis_debounced("org-1")
    assert scheduled is True
    mock_apply_async.assert_called_once_with(
        args=("org-1",), countdown=worker_tasks.ANALYSIS_DEBOUNCE_COUNTDOWN
    )
    assert "analysis_pending:org-1" in fake_redis.store


def test_second_sync_does_not_schedule_duplicate(fake_redis, mock_apply_async):
    assert worker_tasks._schedule_org_analysis_debounced("org-1") is True
    assert worker_tasks._schedule_org_analysis_debounced("org-1") is False
    # Solo el primero encoló.
    assert mock_apply_async.call_count == 1


def test_different_orgs_do_not_share_lock(fake_redis, mock_apply_async):
    assert worker_tasks._schedule_org_analysis_debounced("org-1") is True
    assert worker_tasks._schedule_org_analysis_debounced("org-2") is True
    assert mock_apply_async.call_count == 2


def test_fail_open_when_redis_down(mocker, mock_apply_async):
    mocker.patch("redis.Redis.from_url", return_value=_BrokenRedis())
    # Redis caído: preferimos un análisis de más a perderlo.
    assert worker_tasks._schedule_org_analysis_debounced("org-1") is True
    mock_apply_async.assert_called_once()


def test_release_lock_allows_next_schedule(fake_redis, mock_apply_async):
    assert worker_tasks._schedule_org_analysis_debounced("org-1") is True
    worker_tasks._release_analysis_lock("org-1")
    assert "analysis_pending:org-1" in fake_redis.deleted
    # Liberado el lock, el siguiente sync puede volver a agendar.
    assert worker_tasks._schedule_org_analysis_debounced("org-1") is True
    assert mock_apply_async.call_count == 2
