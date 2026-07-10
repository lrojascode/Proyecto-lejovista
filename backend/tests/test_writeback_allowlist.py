"""F-2: ENABLE_META_WRITEBACK es un flag global — sin un allowlist, encenderlo
escribe de verdad en TODAS las cuentas del parque a la vez, no solo en la
cuenta piloto. `simulate_meta_writes_for` permite restringir la escritura real
a una lista de AdAccount.id mientras se valida el encendido controlado (F-2).
"""
import pytest
from app.config import Settings


def _settings(**overrides):
    base = dict(
        SECRET_KEY="test-secret", ENCRYPTION_KEY="test-encryption-key-32-bytes-ok",
        MOCK_SERVICES=False, ENABLE_META_WRITEBACK=True, WRITEBACK_ACCOUNT_ALLOWLIST="",
    )
    base.update(overrides)
    return Settings(**base)


def test_mock_mode_always_simulates_regardless_of_allowlist():
    s = _settings(MOCK_SERVICES=True, WRITEBACK_ACCOUNT_ALLOWLIST="acc-1")
    assert s.simulate_meta_writes_for("acc-1") is True
    assert s.simulate_meta_writes_for("acc-999") is True


def test_writeback_disabled_always_simulates():
    s = _settings(ENABLE_META_WRITEBACK=False)
    assert s.simulate_meta_writes_for("acc-1") is True


def test_no_allowlist_configured_writes_for_any_account():
    """Sin allowlist: comportamiento anterior a F-2, todas las cuentas escriben real."""
    s = _settings(WRITEBACK_ACCOUNT_ALLOWLIST="")
    assert s.simulate_meta_writes_for("acc-1") is False
    assert s.simulate_meta_writes_for("acc-anything") is False


def test_allowlist_restricts_real_writes_to_listed_accounts():
    s = _settings(WRITEBACK_ACCOUNT_ALLOWLIST="acc-pilot-1, acc-pilot-2")
    assert s.simulate_meta_writes_for("acc-pilot-1") is False
    assert s.simulate_meta_writes_for("acc-pilot-2") is False
    assert s.simulate_meta_writes_for("acc-not-piloted") is True


def test_allowlist_handles_missing_account_id():
    s = _settings(WRITEBACK_ACCOUNT_ALLOWLIST="acc-pilot-1")
    assert s.simulate_meta_writes_for(None) is True
