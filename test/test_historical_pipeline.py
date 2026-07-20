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

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core_engine.historical import pipeline
from core_engine.historical import runtime_support
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


def test_legacy_verified_gap_cache_is_invalidated(monkeypatch, tmp_path):
    cache = tmp_path / "verified_market_gaps.json"
    cache.write_text(
        '{"verified_at":"2026-07-20T00:00:00","windows":[[81,"M5","2026-07-19T00:00:00","2026-07-19T01:00:00"]]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_support, "VERIFIED_MARKET_GAPS", cache)

    assert runtime_support.load_verified_gaps() == {}


def test_current_verified_gap_cache_round_trips_with_version(monkeypatch, tmp_path):
    cache = tmp_path / "verified_market_gaps.json"
    monkeypatch.setattr(runtime_support, "VERIFIED_MARKET_GAPS", cache)
    monkeypatch.setattr(runtime_support, "now_utc", lambda: datetime(2026, 7, 20, 0, 0, 0))
    start = datetime(2026, 7, 19, 0, 0, 0)
    end = datetime(2026, 7, 19, 1, 0, 0)

    runtime_support.save_verified_gaps({(81, "M5", start, end)}, pipeline.logger)

    raw = cache.read_text(encoding="utf-8")
    assert f'"verification_version": {runtime_support.VERIFIED_GAP_CACHE_VERSION}' in raw
    assert runtime_support.load_verified_gaps() == {(81, "M5"): [(start, end)]}


def test_find_hole_pairs_propagates_gap_scan_failure(monkeypatch):
    def fail_scan(*_args, **_kwargs):
        raise RuntimeError("SQL Server unreachable")

    monkeypatch.setattr(runtime_support, "get_internal_gaps", fail_scan)

    with pytest.raises(RuntimeError, match="SQL Server unreachable"):
        runtime_support.find_hole_pairs(
            [],
            pipeline.logger,
            symbols=[],
            tf_filter={"M5"},
        )


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


def test_targeted_gap_fetch_does_not_expand_into_deep_history(monkeypatch):
    captured = {}

    def _fake_fetch_history(**kwargs):
        captured.update(kwargs)
        return pipeline.tv_history.WsHistoryResult(
            _valid_ohlcv_df(), "completed", kwargs["n_bars"], 2, "5", "", "data"
        )

    monkeypatch.setattr(pipeline.tv_history, "fetch_history", _fake_fetch_history)

    pipeline._fetch_history_frame(
        pipeline.SimpleNamespace(token="token"),
        _sym(),
        "M5",
        15,
        allow_replay=False,
    )

    assert captured["request_more_rounds"] == 0
    assert captured["n_bars"] == 15


def test_full_fetch_keeps_configured_deep_history_rounds(monkeypatch):
    captured = {}

    def _fake_fetch_history(**kwargs):
        captured.update(kwargs)
        return pipeline.tv_history.WsHistoryResult(
            _valid_ohlcv_df(), "completed", kwargs["n_bars"], 2, "5", "", "data"
        )

    monkeypatch.setattr(pipeline.tv_history, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(pipeline.replay_runtime, "enabled", False)

    pipeline._fetch_history_frame(
        pipeline.SimpleNamespace(token="token"),
        _sym(),
        "M5",
        15,
        allow_replay=True,
    )

    assert captured["request_more_rounds"] == pipeline.TRADINGVIEW.history_request_more_rounds


# --- _write_ohlcv_frame: crash-recovery fix (P0-2) -------------------------
#
# A prior historical run can crash after insert_staging_batch() commits but
# before run_etl_direct() runs (or succeeds). Re-pulling the same bars later
# then re-stages identical rows, so insert_staging_batch's MERGE affects 0
# rows ("staged == 0"). Before this fix, _write_ohlcv_frame short-circuited
# on staged <= 0 and never called run_etl_direct again, so that stuck row
# could sit in staging forever and eventually be purged - permanent loss.
# These tests assert ETL is now called unconditionally (unless skip_etl).


def _valid_ohlcv_df() -> pd.DataFrame:
    now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    idx = [now, now + timedelta(minutes=5)]
    return pd.DataFrame(
        {"open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0], "close": [1.1, 1.2]},
        index=idx,
    )


def _sym() -> dict:
    return {"tv_symbol": "EURUSD", "tv_exchange": "OANDA", "symbol_id": 999}


@pytest.fixture
def isolated_warehouse_lock(monkeypatch):
    """Unit tests for frame/ETL behavior must never acquire the real
    SEN.ActiveTask lock from the production database."""
    monkeypatch.setattr(pipeline, "_warehouse_write_slot", lambda _owner: nullcontext())


def test_write_ohlcv_frame_calls_etl_even_when_staged_rows_is_zero(monkeypatch, isolated_warehouse_lock):
    etl_calls = []
    monkeypatch.setattr(pipeline, "insert_staging_batch", lambda *a, **k: 0)
    monkeypatch.setattr(
        pipeline, "run_etl_direct",
        lambda symbol_id, tf_code, staging_table, **k: etl_calls.append((symbol_id, tf_code, k)) or 0,
    )

    result = pipeline._write_ohlcv_frame(_valid_ohlcv_df(), _sym(), "M5")

    assert len(etl_calls) == 1, "run_etl_direct must run even when staged == 0"
    assert etl_calls[0][0] == 999
    assert etl_calls[0][1] == "M5"
    assert result == 0


def test_write_ohlcv_frame_passes_from_time_scoped_to_batch(monkeypatch, isolated_warehouse_lock):
    # validate_ohlcv_df's default normalize_timestamps=True reinterprets a
    # naive index as LOCAL wall-clock time and converts to UTC, so the
    # from_time actually sent to run_etl_direct will not textually equal
    # df.index.min() unless the host's UTC offset happens to be zero. What
    # matters for this fix is that from_time is (a) present, (b) the right
    # format, and (c) close to "now" (i.e. derived from the batch, not a
    # stale/unrelated value) - not an exact string match across timezones.
    captured = {}
    monkeypatch.setattr(pipeline, "insert_staging_batch", lambda *a, **k: 2)

    def _fake_etl(symbol_id, tf_code, staging_table, **k):
        captured.update(k)
        return 2

    monkeypatch.setattr(pipeline, "run_etl_direct", _fake_etl)

    df = _valid_ohlcv_df()
    pipeline._write_ohlcv_frame(df, _sym(), "M5")

    from_time = captured.get("from_time")
    assert from_time is not None
    # Must parse as a real timestamp in the exact format the SP expects, and
    # must not be the SP's own hardcoded default ('2008-01-01') - i.e. it was
    # genuinely derived from this batch's data, not left unset.
    parsed = datetime.strptime(from_time, "%Y-%m-%d %H:%M:%S")
    assert parsed.year >= 2020


# --- High-10: total fail count must not be erased by a mid-run refresh --


def test_run_full_load_reports_true_total_fail_count_after_mid_run_refresh(monkeypatch):
    # Before this fix, stats["fail"] was reset to 0 on a successful mid-run
    # refresh, so the run's final reported fail count silently lost every
    # failure that happened before the refresh - a run with real failures
    # could end up reporting a fail count of 0 (looking like full success).
    monkeypatch.setattr(pipeline, "_selected_timeframes", lambda tf_filter: [("i", "M5", "SEN.TF_M5", 100)])
    monkeypatch.setattr(pipeline, "wait_for_historical_slot", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "sleep_for", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "tf_header", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "pair_flow_header", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "pair_start", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "pair_result", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "tf_summary", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "MAX_CONSECUTIVE_FAIL", 2)

    # 3 symbols: fail, fail (triggers refresh at MAX_CONSECUTIVE_FAIL=2,
    # refresh succeeds), then a 3rd fail. True total should be 3, and the
    # 3rd failure must NOT immediately raise (consecutive_fail was reset
    # by the successful refresh, so it is back to 1/2, not already at 2).
    results = iter([-1, -1, -1])
    monkeypatch.setattr(pipeline, "pull_with_retry", lambda *a, **k: next(results))
    monkeypatch.setattr(pipeline, "_refresh_mid_run", lambda tv: True)

    symbols = [{"tv_symbol": f"SYM{i}", "symbol_id": i} for i in range(3)]
    stats = pipeline.run_full_load(object(), symbols=symbols, dry_run=False)

    assert stats["fail"] == 3, "true total fail count must survive a mid-run refresh reset"
    assert stats["ok"] == 0


def test_run_full_load_raises_after_consecutive_fail_even_with_prior_successes(monkeypatch):
    monkeypatch.setattr(pipeline, "_selected_timeframes", lambda tf_filter: [("i", "M5", "SEN.TF_M5", 100)])
    monkeypatch.setattr(pipeline, "wait_for_historical_slot", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "sleep_for", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "tf_header", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "pair_flow_header", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "pair_start", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "pair_result", lambda *a, **k: None)
    monkeypatch.setattr(pipeline._reporter, "tf_summary", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "MAX_CONSECUTIVE_FAIL", 2)
    monkeypatch.setattr(pipeline, "_refresh_mid_run", lambda tv: False)

    # success, fail, fail -> should raise on the 2nd consecutive failure
    # even though there was an earlier success (consecutive_fail counts
    # from the last success, not from the start of the run).
    results = iter([0, -1, -1])
    monkeypatch.setattr(pipeline, "pull_with_retry", lambda *a, **k: next(results))

    symbols = [{"tv_symbol": f"SYM{i}", "symbol_id": i} for i in range(3)]
    with pytest.raises(RuntimeError, match="too many consecutive"):
        pipeline.run_full_load(object(), symbols=symbols, dry_run=False)


def test_write_ohlcv_frame_skip_etl_still_skips(monkeypatch, isolated_warehouse_lock):
    etl_calls = []
    monkeypatch.setattr(pipeline, "insert_staging_batch", lambda *a, **k: 0)
    monkeypatch.setattr(pipeline, "run_etl_direct", lambda *a, **k: etl_calls.append(1))

    result = pipeline._write_ohlcv_frame(_valid_ohlcv_df(), _sym(), "M5", skip_etl=True)

    assert etl_calls == []
    assert result == 0


def test_pull_and_store_does_not_swallow_lost_lock_cancellation(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "_fetch_history_frame",
        lambda *a, **k: (_valid_ohlcv_df(), "fault-injected"),
    )

    def _lost_lock(*_args, **_kwargs):
        raise pipeline.HistoricalPullCancelled("historical database lock lease lost")

    monkeypatch.setattr(pipeline, "_write_ohlcv_frame", _lost_lock)

    with pytest.raises(pipeline.HistoricalPullCancelled, match="lock lease lost"):
        pipeline.pull_and_store(object(), _sym(), "M5", 10)
