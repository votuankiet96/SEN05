from __future__ import annotations

import logging

from core_engine.core.live.telemetry import LiveReporter


def _classify(*, spool_count: int, spool_oldest_age_seconds: float | None):
    reporter = LiveReporter(logging.getLogger("test_live_health"), {})
    return reporter.classify_health(
        recent_errors=0,
        total_errors=0,
        last_hour_accepted=10,
        last_hour_saved=10,
        stale_count=0,
        source_lag_count=0,
        source_lag_entries=[],
        spool_count=spool_count,
        spool_oldest_age_seconds=spool_oldest_age_seconds,
        recent_ws_errors=0,
        total_ws_errors=0,
        n_miss_active=0,
        is_guest=False,
        consecutive_guest_batches=0,
    )


def test_fresh_in_flight_outbox_row_does_not_degrade_live_health():
    status, level, issues = _classify(
        spool_count=1,
        spool_oldest_age_seconds=2.0,
    )

    assert status == "HEALTHY"
    assert level == "SUCCESS"
    assert issues == []


def test_outbox_row_waiting_over_15_minutes_is_critical():
    status, level, issues = _classify(
        spool_count=2,
        spool_oldest_age_seconds=16 * 60,
    )

    assert status == "CRITICAL"
    assert level == "ERROR"
    assert any("Fact" in issue and "16" in issue for issue in issues)


def test_log_block_emits_one_aligned_raw_table_and_one_stable_event(caplog):
    from core_engine.util.logkit.formatter import EventText, RawText

    logger_name = "test_live_report_block"
    reporter = LiveReporter(logging.getLogger(logger_name), {})
    lines = [
        "Sessions : 165/165 answered",
        "Accepted : 11 closed candles across 11 pair(s)",
        "Missing  : none",
        "Retry    : 0 pair(s) / -",
        "Analysis : Batch flow is healthy",
    ]

    with caplog.at_level(logging.INFO, logger=logger_name):
        reporter.log_block("WS LIVE BATCH REPORT #64", lines, logging.INFO)

    raw_records = [r for r in caplog.records if isinstance(r.msg, RawText)]
    event_records = [
        r
        for r in caplog.records
        if r.msg == "%s" and r.args and isinstance(r.args[0], EventText)
    ]

    # Exactly one pre-formatted block, not one record per source line.
    assert len(raw_records) == 1
    assert "BATCH SUMMARY #64" in raw_records[0].msg
    assert raw_records[0].msg.count("\n") > 1

    # Exactly one queryable companion event with a stable (not slugified
    # sentence) event key, independent of the batch number.
    assert len(event_records) == 1
    assert event_records[0].args[0].event == "batch_report"
