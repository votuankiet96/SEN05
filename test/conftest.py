"""Shared pytest fixtures for the whole test suite.

Every CRITICAL record is synchronously persisted to a SQLite outbox before
the logging call returns; HTTP delivery then runs in a fixed background
worker. This suite-wide fixture isolates that durable store and disables real
network delivery for every test.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


# This must run while conftest is imported, before pytest imports test
# modules (many of which create component loggers at module scope). A
# fixture is too late for that: collection would already have attached
# handlers to the real production runtime logs. Use the checked-in example
# config as well, so unit tests cannot inherit production DB/webhook secrets.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_APP_ROOT = Path(tempfile.mkdtemp(prefix="dp_program_pytest_"))
(_TEST_APP_ROOT / "config").mkdir(parents=True, exist_ok=True)
shutil.copy2(
    _REPO_ROOT / "config" / "dp_provider.env.example",
    _TEST_APP_ROOT / "config" / "dp_provider.env",
)
os.environ["DP_APP_ROOT"] = str(_TEST_APP_ROOT)
os.environ["DP_DISABLE_CONSOLE_LOG"] = "1"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_APP_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_critical_alert_outbox(tmp_path, monkeypatch):
    import core_engine.util.notify.critical_outbox as outbox_mod

    fake = outbox_mod.CriticalAlertOutbox(
        db_path=tmp_path / "critical_alerts_outbox.db",
        status_log_path=tmp_path / "critical_undelivered.log",
    )
    fake.init()
    # Never touch the real network from a test - default to "delivery
    # failed" so outbox-empty/outbox-populated behavior stays observable
    # without depending on real webhook connectivity.
    monkeypatch.setattr(fake, "send_one", lambda message: False)
    monkeypatch.setattr(outbox_mod, "_OUTBOX", fake)
    monkeypatch.setattr(outbox_mod, "_DISPATCHER", outbox_mod._CriticalDeliveryDispatcher())
    yield fake
    monkeypatch.setattr(outbox_mod, "_OUTBOX", None)
