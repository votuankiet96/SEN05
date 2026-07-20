"""Tests for the core_engine.historical.engine / .pipeline split.

historical/engine.py's _set_replay_runtime applies --replay-* CLI
overrides; historical/pipeline.py's _fetch_history_frame reads them when
deciding whether to crawl replay history for a pair. Before this split,
both lived in one file and the override worked via
`globals()[name] = value` - a single module's globals as ad hoc shared
state. Splitting engine.py and pipeline.py apart broke that: pipeline.py
imports its own copy of each TV_WS_REPLAY_* settings constant, so
mutating engine.py's copy would have silently left pipeline.py's copy
(and therefore every actual fetch) unaffected. The fix routes both sides
through one shared, mutable `replay_runtime` object instead of a name
rebind - these tests exist to keep that working.
"""

from __future__ import annotations

from core_engine.historical import pipeline
from core_engine.historical.engine import _set_replay_runtime


def _reset_replay_runtime():
    from core_engine.settings import HISTORICAL

    pipeline.replay_runtime.enabled = HISTORICAL.replay_enabled
    pipeline.replay_runtime.tfs = set(HISTORICAL.replay_tfs)
    pipeline.replay_runtime.endpoint = HISTORICAL.replay_endpoint
    pipeline.replay_runtime.start_date = HISTORICAL.replay_start_date
    pipeline.replay_runtime.window_bars = HISTORICAL.replay_window_bars
    pipeline.replay_runtime.step_bars = HISTORICAL.replay_step_bars
    pipeline.replay_runtime.max_windows_per_pair = HISTORICAL.replay_max_windows_per_pair
    pipeline.replay_runtime.timeout_sec = HISTORICAL.replay_timeout_sec


def test_set_replay_runtime_enabled_is_visible_in_pipeline_module():
    _reset_replay_runtime()
    try:
        _set_replay_runtime("TV_WS_REPLAY_ENABLED", False)
        assert pipeline.replay_runtime.enabled is False
        _set_replay_runtime("TV_WS_REPLAY_ENABLED", True)
        assert pipeline.replay_runtime.enabled is True
    finally:
        _reset_replay_runtime()


def test_set_replay_runtime_tfs_is_visible_in_pipeline_module():
    _reset_replay_runtime()
    try:
        _set_replay_runtime("TV_WS_REPLAY_TFS", {"M5", "H1"})
        assert pipeline.replay_runtime.tfs == {"M5", "H1"}
    finally:
        _reset_replay_runtime()


def test_set_replay_runtime_updates_every_mapped_attribute():
    _reset_replay_runtime()
    try:
        _set_replay_runtime("TV_WS_REPLAY_ENDPOINT", "data")
        _set_replay_runtime("TV_WS_REPLAY_START_DATE", "2020-01-01")
        _set_replay_runtime("TV_WS_REPLAY_WINDOW_BARS", 1234)
        _set_replay_runtime("TV_WS_REPLAY_STEP_BARS", 999)
        _set_replay_runtime("TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR", 7)
        _set_replay_runtime("TV_WS_REPLAY_TIMEOUT_SEC", 12.5)

        assert pipeline.replay_runtime.endpoint == "data"
        assert pipeline.replay_runtime.start_date == "2020-01-01"
        assert pipeline.replay_runtime.window_bars == 1234
        assert pipeline.replay_runtime.step_bars == 999
        assert pipeline.replay_runtime.max_windows_per_pair == 7
        assert pipeline.replay_runtime.timeout_sec == 12.5
    finally:
        _reset_replay_runtime()


def test_set_replay_runtime_also_updates_reporter_replay_fields():
    # _set_replay_runtime has a second effect beyond replay_runtime: it
    # keeps the operator-facing HistoricalReporter's replay_enabled/
    # replay_tfs display fields in sync too.
    _reset_replay_runtime()
    try:
        _set_replay_runtime("TV_WS_REPLAY_ENABLED", False)
        assert pipeline._reporter.replay_enabled is False
        _set_replay_runtime("TV_WS_REPLAY_TFS", {"m30"})
        assert pipeline._reporter.replay_tfs == {"M30"}
    finally:
        _reset_replay_runtime()


def test_unknown_replay_name_is_ignored_without_error():
    _reset_replay_runtime()
    _set_replay_runtime("NOT_A_REPLAY_SETTING", 123)  # must not raise
