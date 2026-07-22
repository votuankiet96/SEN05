"""Tests for the Medium-15 fix in core_engine.core.live.engine: the candle-flow
table (_append_live_table_text) writes directly to live_fetching.log via a
raw file append, bypassing the ResilientRotatingFileHandler that
`logger` (the same file) is configured with. Rotation only used to get
checked inside that handler's own emit() - i.e. only when a real
logger.info()/warning()/etc. call happened - so the raw appends (the
majority of this file's actual volume, since a full candle table is
written per batch) could grow the file far past the intended maxBytes cap
before the size check ever fired. _maybe_rotate_live_log() re-checks after
every raw append instead.
"""

from __future__ import annotations

import logging

import pytest

import core_engine.core.live.engine as live_engine
from core_engine.util.logkit.handlers import ResilientRotatingFileHandler


@pytest.fixture
def isolated_live_logger(tmp_path, monkeypatch):
    """Point live_engine.logger/WS_LOG_FILE at a throwaway rotating
    handler + file instead of the real runtime log, so this test can grow
    the file past maxBytes without touching anything real."""
    log_file = tmp_path / "live_fetching.log"
    handler = ResilientRotatingFileHandler(str(log_file), maxBytes=200, backupCount=3, encoding="utf-8")

    fake_logger = logging.getLogger("test_live_log_rotation")
    fake_logger.handlers.clear()
    fake_logger.addHandler(handler)
    fake_logger.setLevel(logging.INFO)

    monkeypatch.setattr(live_engine, "logger", fake_logger)
    monkeypatch.setattr(live_engine, "WS_LOG_FILE", str(log_file))
    yield log_file
    fake_logger.handlers.clear()


def test_live_log_rotating_handler_finds_the_attached_handler(isolated_live_logger):
    handler = live_engine._live_log_rotating_handler()
    assert isinstance(handler, ResilientRotatingFileHandler)


def test_maybe_rotate_triggers_rollover_once_raw_appends_exceed_max_bytes(isolated_live_logger, tmp_path):
    log_file = isolated_live_logger

    # Simulate what _append_live_table_text does: raw appends with no
    # logger.info() call in between, growing well past maxBytes=200.
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write("x" * 250 + "\n")

    live_engine._maybe_rotate_live_log()

    rotated = tmp_path / "live_fetching.log.1"
    assert rotated.exists(), "doRollover() must have fired once the raw-appended file exceeded maxBytes"
    # The live file itself should be reset (rollover truncates/renames).
    assert log_file.stat().st_size < 250


def test_maybe_rotate_does_nothing_below_max_bytes(isolated_live_logger, tmp_path):
    log_file = isolated_live_logger
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write("short\n")

    live_engine._maybe_rotate_live_log()

    assert not (tmp_path / "live_fetching.log.1").exists()


def test_maybe_rotate_is_safe_with_no_rotating_handler_attached(monkeypatch):
    fake_logger = logging.getLogger("test_live_log_rotation_no_handler")
    fake_logger.handlers.clear()
    monkeypatch.setattr(live_engine, "logger", fake_logger)
    live_engine._maybe_rotate_live_log()  # must not raise
