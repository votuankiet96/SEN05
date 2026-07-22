"""Shared pytest fixtures for the whole test suite.

Every core_engine.util.logkit.get_logger() call attaches the shared
CriticalDiscordHandler singleton, which (as of the P0-6 durable-outbox
fix) persists to a real SQLite file under runtime/cache and performs a
real synchronous HTTP POST on every CRITICAL log record - on ANY
component logger, not just ones a given test file created itself. Without
this fixture, any test anywhere in the suite that triggers logger.critical()
on a shared logger (e.g. util/supervisor/engine.py's "system" logger) would
write to the real outbox file and attempt a real network call. This
autouse, suite-wide fixture resets the outbox singleton to a tmp_path-backed
instance with network delivery stubbed out for every test.
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
    yield fake
    monkeypatch.setattr(outbox_mod, "_OUTBOX", None)
