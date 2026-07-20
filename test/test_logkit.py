"""Tests for core_engine.logkit: the get_logger() factory, the shared
errors.log WARNING+ aggregate handler, and the CRITICAL -> Discord alert
handler added when logging was standardized.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from core_engine.logkit.factory import _component_level, get_logger
from core_engine.logkit.handlers import critical_discord_handler, errors_aggregate_handler
from core_engine.logkit import tables


@pytest.fixture
def unique_logger_name(request):
    # Every get_logger() call is a no-op past the first for a given name
    # (logging.getLogger caches by name), so each test needs its own name.
    name = f"test_logkit_{request.node.name}"
    yield name
    logging.getLogger(name).handlers.clear()
    logging.Logger.manager.loggerDict.pop(name, None)


@pytest.fixture(autouse=True)
def isolated_errors_aggregate_handler(tmp_path, monkeypatch):
    """Every get_logger() call attaches the shared errors.log handler.

    Without this, running this test file would keep appending test noise
    into the real runtime/logs/system/errors.log. Reset the module-level
    singleton and point it at a throwaway file for every test here, then
    reset it again afterward so real usage rebuilds the real handler.
    """
    import core_engine.logkit.handlers as handlers_mod

    monkeypatch.setattr(handlers_mod, "_ERRORS_AGGREGATE_HANDLER", None)
    monkeypatch.setattr("core_engine.settings.SYSTEM_LOG_DIR", tmp_path)
    yield
    monkeypatch.setattr(handlers_mod, "_ERRORS_AGGREGATE_HANDLER", None)


def test_get_logger_is_idempotent_for_the_same_name(unique_logger_name, tmp_path):
    log_file = str(tmp_path / f"{unique_logger_name}.log")
    first = get_logger(unique_logger_name, log_file, console=False)
    second = get_logger(unique_logger_name, log_file, console=False)
    assert first is second
    # Handlers must not double up on repeated get_logger() calls.
    assert first.handlers.count(first.handlers[0]) == 1


def test_get_logger_writes_to_its_own_file(unique_logger_name, tmp_path):
    log_file = tmp_path / f"{unique_logger_name}.log"
    log = get_logger(unique_logger_name, str(log_file), console=False)
    log.info("hello from %s", unique_logger_name)
    for handler in log.handlers:
        handler.flush()
    assert log_file.exists()
    assert "hello from" in log_file.read_text(encoding="utf-8")


def test_warning_and_above_reach_the_shared_errors_log(unique_logger_name, tmp_path):
    fake_errors_log = tmp_path / "errors.log"
    log_file = str(tmp_path / f"{unique_logger_name}.log")
    log = get_logger(unique_logger_name, log_file, console=False)
    log.info("this should NOT appear in errors.log")
    log.warning("this warning SHOULD appear in errors.log")
    log.error("this error SHOULD appear in errors.log")
    for handler in log.handlers:
        handler.flush()

    assert fake_errors_log.exists()
    content = fake_errors_log.read_text(encoding="utf-8")
    assert "this should NOT appear" not in content
    assert "this warning SHOULD appear" in content
    assert "this error SHOULD appear" in content


def test_critical_log_triggers_discord_alert_without_hitting_network(
    unique_logger_name, tmp_path, isolated_critical_alert_outbox, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        isolated_critical_alert_outbox, "send_one",
        lambda message: calls.append(message) or True,
    )
    log_file = str(tmp_path / f"{unique_logger_name}.log")
    log = get_logger(unique_logger_name, log_file, console=False)
    log.critical("simulated critical failure")

    assert len(calls) == 1
    assert unique_logger_name in calls[0]
    assert "simulated critical failure" in calls[0]
    # A successful send must ack (delete) the outbox row, not leave it pending.
    assert isolated_critical_alert_outbox.status()["pending_count"] == 0


def test_critical_discord_handler_swallows_send_failures(
    unique_logger_name, tmp_path, isolated_critical_alert_outbox, monkeypatch
):
    # A broken Discord channel must never crash the caller's log statement,
    # and the failed alert must remain in the outbox for a later retry
    # rather than being lost.
    monkeypatch.setattr(
        isolated_critical_alert_outbox, "send_one",
        lambda message: (_ for _ in ()).throw(RuntimeError("webhook down")),
    )
    log_file = str(tmp_path / f"{unique_logger_name}.log")
    log = get_logger(unique_logger_name, log_file, console=False)
    log.critical("this must not raise")  # no exception expected

    assert isolated_critical_alert_outbox.status()["pending_count"] == 1


def test_errors_aggregate_handler_is_a_singleton():
    assert errors_aggregate_handler() is errors_aggregate_handler()


def test_critical_discord_handler_is_a_singleton():
    assert critical_discord_handler() is critical_discord_handler()


def test_component_level_defaults_to_global_log_level():
    with patch("core_engine.logkit.factory.LOGGING") as mock_logging:
        mock_logging.level = "INFO"
        with patch("core_engine.logkit.factory.env_str", return_value=""):
            assert _component_level("live_fetching") == logging.INFO


def test_component_level_honors_per_component_override():
    def fake_env_str(name):
        return "DEBUG" if name == "LOG_LEVEL_LIVE_FETCHING" else ""

    with patch("core_engine.logkit.factory.env_str", side_effect=fake_env_str):
        assert _component_level("live_fetching") == logging.DEBUG


def test_tables_reexports_are_the_logkit_canonical_helpers():
    # cell()/kv() moved here from private per-file duplicates; make sure the
    # package still exposes them (a smoke check, not a behavior test - see
    # test_tables.py for behavior coverage).
    assert callable(tables.cell)
    assert callable(tables.kv)
